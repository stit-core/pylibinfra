"""Worker: батч-дренаж outbox. tick() вычитывает unpublished, публикует каждое
в natskit.Bus и помечает published; ошибка публикации прерывает тик, не помечая
событие (повторная попытка на следующем тике — at-least-once). run_loop()
крутит tick с интервалом из конфига и снимается отменой задачи на остановке."""

import asyncio
import time

from opentelemetry import trace
from opentelemetry.trace import SpanKind

from pylibinfra.logkit import Logger, nop
from pylibinfra.natskit import Bus as NatsBus

from .config import Config
from .metrics import OutboxInstruments
from .outbox import Message, Store


class Worker:
    def __init__(self, store: Store, bus: NatsBus, cfg: Config, log: Logger | None = None) -> None:
        self._store = store
        self._bus = bus
        self._batch_size = cfg.batch_size
        self._interval = cfg.interval
        self._log = log or nop()
        self._instr = OutboxInstruments()
        self._tracer = trace.get_tracer("app")

    async def tick(self) -> None:
        start = time.perf_counter()
        with self._tracer.start_as_current_span("outboxkit.tick", kind=SpanKind.INTERNAL):
            try:
                msgs = await self._store.list_unpublished(self._batch_size)
            except Exception:
                self._instr.batch_duration.record(time.perf_counter() - start)
                raise
            self._instr.batch_size.record(len(msgs))

            try:
                for msg in msgs:
                    await self._process(msg)
            finally:
                self._instr.batch_duration.record(time.perf_counter() - start)

    async def _process(self, msg: Message) -> None:
        subj_attr = {"subject": msg.subject}
        pub_start = time.perf_counter()
        try:
            await self._bus.publish(msg.subject, msg.payload)
        except Exception:
            self._instr.processed_total.add(1, {"subject": msg.subject, "status": "publish_error"})
            self._instr.publish_duration.record(time.perf_counter() - pub_start, subj_attr)
            raise
        try:
            await self._store.mark_published(msg.id)
        except Exception:
            self._instr.processed_total.add(1, {"subject": msg.subject, "status": "mark_error"})
            self._instr.publish_duration.record(time.perf_counter() - pub_start, subj_attr)
            raise
        self._instr.processed_total.add(1, {"subject": msg.subject, "status": "published"})
        self._instr.publish_duration.record(time.perf_counter() - pub_start, subj_attr)

    async def run_loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Сбой тика не валит воркер: следующий тик повторит дренаж.
                self._log.error("outboxkit: worker tick error", error=str(exc))
            await asyncio.sleep(self._interval)
