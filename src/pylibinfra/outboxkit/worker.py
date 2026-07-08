"""Worker: батч-дренаж outbox. run() вычитывает unpublished, публикует через
Transport (JetStream) и помечает published только подтверждённые PubAck'ом.
OUTBOX_PUBLISH_MODE выбирает batch (async, best-effort порядок) или ordered
(строгий порядок, стоп на первой ошибке). run_loop() крутит run() с интервалом
из конфига (self-managed путь OutboxProvider); multi-instance гоняет run() через
timerkit-джоб (Outbox.job())."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from pylibinfra.logkit import Logger, nop

from .config import Config, PublishMode
from .metrics import OutboxInstruments
from .outbox import Failure, Message, Repository
from .transport import OutMsg, Transport

_ERR_MAX = 1000

_Failed = list[tuple[Message, BaseException]]


class Worker:
    def __init__(
        self, repo: Repository, tr: Transport, cfg: Config, log: Logger | None = None
    ) -> None:
        self._repo = repo
        self._tr = tr
        self._batch_size = cfg.batch_size
        self._interval = cfg.interval
        self._mode = cfg.publish_mode
        self._max_attempts = cfg.max_attempts
        self._log = log or nop()
        self._instr = OutboxInstruments()

    async def run(self) -> None:
        start = time.perf_counter()
        try:
            msgs = await self._repo.list_unpublished(self._batch_size)
        except Exception as exc:
            self._instr.batch_duration.record(time.perf_counter() - start)
            raise RuntimeError(f"outboxkit worker: list: {exc}") from exc
        self._instr.batch_size.record(len(msgs))
        if not msgs:
            self._instr.batch_duration.record(time.perf_counter() - start)
            return

        out = [OutMsg(id=m.id, subject=m.subject, payload=_to_bytes(m.payload)) for m in msgs]

        if self._mode == PublishMode.ORDERED:
            acked, failed, first_err = await self._publish_ordered(msgs, out)
        else:
            acked, failed, first_err = await self._publish_batch(msgs, out)

        mark_err = await self._mark_all(acked)
        if mark_err is not None and first_err is None:
            first_err = mark_err
        rec_err = await self._record_failures(failed)
        if rec_err is not None and first_err is None:
            first_err = rec_err

        self._instr.batch_duration.record(time.perf_counter() - start)
        if first_err is not None:
            raise first_err

    async def _publish_batch(
        self, msgs: list[Message], out: list[OutMsg]
    ) -> tuple[list[Message], _Failed, Exception | None]:
        # async-батч: все улетают разом, метятся только подтверждённые PubAck'ом;
        # неподтверждённые уходят на ретрай (дедуп по Msg-Id — без дублей).
        pub_start = time.perf_counter()
        try:
            results = await self._tr.publish_batch(out)
        except Exception as exc:
            self._instr.publish_duration.record(time.perf_counter() - pub_start)
            return [], [], RuntimeError(f"outboxkit worker: publish batch: {exc}")
        self._instr.publish_duration.record(time.perf_counter() - pub_start)

        acked: list[Message] = []
        failed: _Failed = []
        first_err: Exception | None = None
        for i, m in enumerate(msgs):
            perr = results[i]
            if perr is not None:
                self._processed(m.subject, "publish_error")
                failed.append((m, perr))
                if first_err is None:
                    first_err = RuntimeError(
                        f"outboxkit worker: publish {m.subject} (id={m.id}): {perr}"
                    )
                continue
            acked.append(m)
        return acked, failed, first_err

    async def _publish_ordered(
        self, msgs: list[Message], out: list[OutMsg]
    ) -> tuple[list[Message], _Failed, Exception | None]:
        # последовательная публикация со стопом на первой ошибке, чтобы не
        # персистить более позднее событие раньше неудавшегося.
        pub_start = time.perf_counter()
        acked: list[Message] = []
        try:
            for i, m in enumerate(msgs):
                try:
                    await self._tr.publish(out[i])
                except Exception as exc:
                    self._processed(m.subject, "publish_error")
                    return (
                        acked,
                        [(m, exc)],
                        RuntimeError(f"outboxkit worker: publish {m.subject} (id={m.id}): {exc}"),
                    )
                acked.append(m)
            return acked, [], None
        finally:
            self._instr.publish_duration.record(time.perf_counter() - pub_start)

    async def _mark_all(self, acked: list[Message]) -> Exception | None:
        if not acked:
            return None
        ids = [m.id for m in acked]
        try:
            await self._repo.mark_published(ids)
        except Exception as exc:
            for m in acked:
                self._processed(m.subject, "mark_error")
            return RuntimeError(f"outboxkit worker: mark published: {exc}")
        for m in acked:
            self._processed(m.subject, "published")
        return None

    async def _record_failures(self, failed: _Failed) -> Exception | None:
        if not failed:
            return None
        fs: list[Failure] = []
        for m, err in failed:
            dead = self._max_attempts > 0 and m.attempts + 1 >= self._max_attempts
            fs.append(Failure(id=m.id, err=_trunc_err(err), dead=dead))
            if dead:
                self._instr.dead_total.add(1, {"subject": m.subject})
        try:
            await self._repo.record_failures(fs)
        except Exception as exc:
            return RuntimeError(f"outboxkit worker: record failures: {exc}")
        return None

    def _processed(self, subject: str, status: str) -> None:
        self._instr.processed_total.add(1, {"subject": subject, "status": status})

    async def run_loop(self) -> None:
        while True:
            try:
                await self.run()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Сбой тика не валит воркер: следующий тик повторит дренаж.
                self._log.error("outboxkit: worker tick error", error=str(exc))
            await asyncio.sleep(self._interval)


def _to_bytes(payload: Any) -> bytes:
    # pgstore отдаёт payload сырым JSON-текстом (публикуем его как есть, зеркаля Go
    # json.RawMessage); фейки/доменный код могут отдать готовый объект — сериализуем.
    if isinstance(payload, bytes | bytearray):
        return bytes(payload)
    if isinstance(payload, str):
        return payload.encode()
    return json.dumps(payload, separators=(",", ":")).encode()


def _trunc_err(err: BaseException) -> str:
    s = str(err)
    return s[:_ERR_MAX] if len(s) > _ERR_MAX else s
