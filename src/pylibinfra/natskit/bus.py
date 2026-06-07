from __future__ import annotations

import asyncio
import json
import time
import traceback
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from nats.aio.msg import Msg
from opentelemetry.trace import Status, StatusCode

from pylibinfra.logkit import Logger, nop

from .config import Config
from .conn import Conn
from .instrumentation import NatsInstruments

Handler = Callable[[bytes], Awaitable[None]]


class Bus(Protocol):
    async def publish(self, subject: str, payload: Any) -> None: ...
    async def subscribe(self, subject: str, handler: Handler) -> None: ...


class NatsBus:
    def __init__(self, conn: Conn, log: Logger | None = None, cfg: Config | None = None) -> None:
        cfg = cfg or Config()
        max_in_flight = cfg.backpressure.max_in_flight
        if max_in_flight <= 0:
            max_in_flight = 100
        self._conn = conn.raw
        self._log = log or nop()
        self._sem = asyncio.Semaphore(max_in_flight)
        self._instr = NatsInstruments()
        # Удерживаем ссылки на handler-таски: без них asyncio собирает их GC ещё
        # до завершения (loop держит лишь weakref).
        self._tasks: set[asyncio.Task[None]] = set()

    async def publish(self, subject: str, payload: Any) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode()

        status = "ok"
        start = time.perf_counter()
        with self._instr.publish_span(subject, data) as (span, headers):
            try:
                await self._conn.publish(subject, data, headers=headers)
            except Exception as exc:
                status = "error"
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise RuntimeError(f"natskit: publish: {exc}") from exc
            finally:
                dur = time.perf_counter() - start
                self._instr.published_total.add(1, {"subject": subject, "status": status})
                self._instr.publish_duration.record(dur, {"subject": subject})

    async def subscribe(self, subject: str, handler: Handler) -> None:
        async def cb(msg: Msg) -> None:
            await self._sem.acquire()
            task = asyncio.create_task(self._handle_one(msg, subject, handler))
            self._tasks.add(task)
            task.add_done_callback(self._on_done)

        await self._conn.subscribe(subject, cb=cb)

    def _on_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        self._sem.release()

    async def _handle_one(self, msg: Msg, subject: str, handler: Handler) -> None:
        subj_attr = {"subject": subject}
        self._instr.inflight.add(1, subj_attr)
        span = self._instr.consumer_span(subject, msg.data, dict(msg.headers or {}))
        start = time.perf_counter()
        status = "ok"
        try:
            try:
                await handler(msg.data)
            except Exception as exc:
                # Panic-recovery: handler-сбой не валит подписку, лог оставляет
                # след, чтобы заметить subject с устойчивыми падениями.
                status = "handler_error"
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                self._log.error(
                    "natskit: handler error",
                    subject=subject,
                    err=str(exc),
                    stack=traceback.format_exc(),
                )
        finally:
            dur = time.perf_counter() - start
            span.end()
            self._instr.consumed_total.add(1, {"subject": subject, "status": status})
            self._instr.handle_duration.record(dur, subj_attr)
            self._instr.inflight.add(-1, subj_attr)


async def publish_json[T](bus: Bus, subject: str, msg: T) -> None:
    await bus.publish(subject, msg)


async def subscribe_json[T](
    bus: Bus, subject: str, handler: Callable[[T], Awaitable[None]]
) -> None:
    async def wrap(payload: bytes) -> None:
        v: T = json.loads(payload)
        await handler(v)

    await bus.subscribe(subject, wrap)
