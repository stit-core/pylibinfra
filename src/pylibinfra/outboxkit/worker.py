"""Drain-сторона outbox'а: тик = одна транзакция list → publish → mark/record."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field

from pylibinfra.logkit import Logger, nop
from pylibinfra.pgkit import Transactor

from .config import Config, PublishMode
from .errors import BusyError, OutboxError
from .metrics import OutboxInstruments
from .outbox import Failure, Message, Repository
from .transport import OutMsg, Transport

# Запас поверх дедлайна тика на фазу mark/commit: она переживает отмену вызывающего.
COMMIT_GRACE = 5.0

# Верхняя граница текста ошибки в `last_error`.
MAX_LAST_ERROR = 1000


@dataclass(slots=True)
class TickResult:
    """Исходы тика, которые становятся метриками только после коммита."""

    published: list[Message] = field(default_factory=list)
    dead: list[Message] = field(default_factory=list)


_Failed = list[tuple[Message, BaseException]]


class Worker:
    """Тик воркера: транзакция, в которой строки заперты до конца публикации.

    Ошибка публикации не откатывает транзакцию — mark подтверждённых и запись неудач
    коммитятся, а ошибка поднимается уже после коммита. Откат только на ошибке хранилища.
    """

    def __init__(
        self,
        instance: str,
        repo: Repository,
        tr: Transport,
        tx: Transactor,
        cfg: Config,
        log: Logger | None = None,
        instr: OutboxInstruments | None = None,
    ) -> None:
        self._instance = instance
        self._repo = repo
        self._tr = tr
        self._tx = tx
        self._batch_size = cfg.batch_size
        self._mode = cfg.publish_mode
        self._max_attempts = cfg.max_attempts
        self._tick_timeout = cfg.tick_timeout
        self._retry_backoff = cfg.retry_backoff
        self._retry_max_backoff = cfg.retry_max_backoff
        self._log = (log or nop()).with_(instance=instance)
        self._instr = instr or OutboxInstruments(instance)

    async def run(self) -> None:
        """Один тик. Отмена вызывающего не откатывает уже подтверждённые публикации.

        Тик живёт отдельной таской, поэтому остановка сервиса не рвёт фазу
        mark/commit: ей даётся `OUTBOX_TICK_TIMEOUT + COMMIT_GRACE` на завершение.
        """
        stopping = asyncio.Event()
        tick = asyncio.create_task(self._tick_tx(stopping), name=f"outboxkit.tick.{self._instance}")
        try:
            await asyncio.shield(tick)
        except asyncio.CancelledError:
            stopping.set()
            done, _ = await asyncio.wait({tick}, timeout=self._tick_timeout + COMMIT_GRACE)
            if not done:
                # Запас исчерпан: незавершённая таска иначе живёт до конца процесса.
                tick.cancel()
            with contextlib.suppress(BaseException):
                await tick
            raise

    async def _tick_tx(self, stopping: asyncio.Event) -> None:
        start = asyncio.get_running_loop().time()
        result = TickResult()
        publish_err: Exception | None = None

        async def run_tick() -> None:
            nonlocal result, publish_err
            result, publish_err = await self._tick(stopping)

        try:
            await self._tx.with_tx(run_tick)
        except BusyError:
            # ordered: голову очереди держит другой воркер — тик просто пропускается.
            return
        finally:
            self._instr.batch_duration.record(
                asyncio.get_running_loop().time() - start, self._instr.attrs
            )
        self._record_committed(result)
        if publish_err is not None:
            raise publish_err

    async def _tick(self, stopping: asyncio.Event) -> tuple[TickResult, Exception | None]:
        deadline = asyncio.get_running_loop().time() + self._tick_timeout
        msgs = await self._list(deadline)
        self._instr.batch_size.record(len(msgs), self._instr.attrs)
        if not msgs:
            return TickResult(), None

        out = [OutMsg(id=m.id, subject=m.subject, payload=m.payload) for m in msgs]
        if self._mode == PublishMode.ORDERED:
            acked, failed, publish_err = await self._publish_ordered(msgs, out, deadline, stopping)
        else:
            acked, failed, publish_err = await self._publish_batch(msgs, out, deadline)

        await self._mark_all(acked)
        dead = await self._record_failures(failed)
        return TickResult(published=acked, dead=dead), publish_err

    async def _list(self, deadline: float) -> list[Message]:
        try:
            return await asyncio.wait_for(
                self._repo.list_unpublished(self._batch_size, self._mode), _left(deadline)
            )
        except BusyError:
            raise
        except Exception as exc:
            raise OutboxError(f"outboxkit worker: list: {exc}") from exc

    async def _publish_batch(
        self, msgs: list[Message], out: list[OutMsg], deadline: float
    ) -> tuple[list[Message], _Failed, Exception | None]:
        """Async-батч: метятся только подтверждённые `PubAck`'ом, остальные уходят на ретрай."""
        start = asyncio.get_running_loop().time()
        try:
            results = await asyncio.wait_for(self._tr.publish_batch(out), _left(deadline))
        except Exception as exc:
            return [], [], OutboxError(f"outboxkit worker: publish batch: {exc}")
        finally:
            self._instr.publish_duration.record(
                asyncio.get_running_loop().time() - start, self._instr.attrs
            )
        if len(results) != len(msgs):
            return (
                [],
                [],
                OutboxError(
                    f"outboxkit worker: transport returned {len(results)} results "
                    f"for {len(msgs)} messages"
                ),
            )

        acked: list[Message] = []
        failed: _Failed = []
        first_err: Exception | None = None
        for msg, err in zip(msgs, results, strict=True):
            if err is None:
                acked.append(msg)
                continue
            self._processed(msg.subject, "publish_error")
            failed.append((msg, err))
            if first_err is None:
                first_err = OutboxError(
                    f"outboxkit worker: publish {msg.subject} (id={msg.id}): {err}"
                )
        return acked, failed, first_err

    async def _publish_ordered(
        self, msgs: list[Message], out: list[OutMsg], deadline: float, stopping: asyncio.Event
    ) -> tuple[list[Message], _Failed, Exception | None]:
        """Последовательная публикация со стопом на первой ошибке: порядок важнее хвоста батча."""
        start = asyncio.get_running_loop().time()
        acked: list[Message] = []
        try:
            for msg, out_msg in zip(msgs, out, strict=True):
                if stopping.is_set() or _left(deadline) <= 0:
                    break
                try:
                    await asyncio.wait_for(self._tr.publish(out_msg), _left(deadline))
                except Exception as exc:
                    self._processed(msg.subject, "publish_error")
                    return (
                        acked,
                        [(msg, exc)],
                        OutboxError(
                            f"outboxkit worker: publish {msg.subject} (id={msg.id}): {exc}"
                        ),
                    )
                acked.append(msg)
        finally:
            self._instr.publish_duration.record(
                asyncio.get_running_loop().time() - start, self._instr.attrs
            )
        return acked, [], None

    async def _mark_all(self, acked: list[Message]) -> None:
        if not acked:
            return
        try:
            await self._repo.mark_published([m.ref() for m in acked])
        except Exception as exc:
            for msg in acked:
                self._processed(msg.subject, "mark_error")
            raise OutboxError(f"outboxkit worker: mark published: {exc}") from exc

    async def _record_failures(self, failed: _Failed) -> list[Message]:
        if not failed:
            return []
        failures: list[Failure] = []
        dead: list[Message] = []
        for msg, err in failed:
            is_dead = self._max_attempts > 0 and msg.attempts + 1 >= self._max_attempts
            failures.append(
                Failure(
                    ref=msg.ref(),
                    err=trunc_error(err),
                    retry_after=self.retry_after(msg.attempts),
                    dead=is_dead,
                )
            )
            if is_dead:
                dead.append(msg)
        try:
            await self._repo.record_failures(failures)
        except Exception as exc:
            raise OutboxError(f"outboxkit worker: record failures: {exc}") from exc
        return dead

    def retry_after(self, attempts: int) -> float:
        """Пауза до следующей попытки: `OUTBOX_RETRY_BACKOFF × 2^attempts` до потолка.

        Без неё неудачная строка ретраится каждый тик и выжигает `OUTBOX_MAX_ATTEMPTS`
        за секунды недоступности брокера.
        """
        if self._retry_backoff <= 0:
            return 0.0
        ceiling = self._retry_max_backoff
        delay = self._retry_backoff * float(2 ** min(attempts, 60))
        if ceiling > 0:
            return min(delay, ceiling)
        return delay

    def _record_committed(self, result: TickResult) -> None:
        """Счётчики состоявшихся исходов — только после коммита тика."""
        for msg in result.published:
            self._processed(msg.subject, "published")
        for msg in result.dead:
            self._instr.dead_total.add(1, {**self._instr.attrs, "subject": msg.subject})

    def _processed(self, subject: str, status: str) -> None:
        self._instr.processed_total.add(
            1, {**self._instr.attrs, "subject": subject, "status": status}
        )


def trunc_error(err: BaseException) -> str:
    """Текст ошибки для `last_error`, обрезанный до `MAX_LAST_ERROR` символов."""
    text = str(err)
    return text[:MAX_LAST_ERROR]


def _left(deadline: float) -> float:
    return max(deadline - asyncio.get_running_loop().time(), 0.0)
