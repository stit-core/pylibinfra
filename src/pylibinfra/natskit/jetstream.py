from __future__ import annotations

import asyncio
import json
import time
import traceback
from collections.abc import Awaitable, Callable
from typing import Protocol

from nats.aio.msg import Msg
from nats.js import api
from nats.js.client import JetStreamContext
from opentelemetry.trace import Status, StatusCode

from pylibinfra.logkit import Logger, nop

from .config import Config
from .conn import Conn
from .instrumentation import NatsInstruments

Handler = Callable[[bytes], Awaitable[None]]


class JetStreamDisabledError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("natskit: jetstream is disabled")


class StreamBus(Protocol):
    async def publish_stream(self, subject: str, payload: bytes) -> None: ...
    async def subscribe_stream(
        self, stream: str, subject: str, durable: str, handler: Handler
    ) -> None: ...


class DisabledStreamBus:
    async def publish_stream(self, subject: str, payload: bytes) -> None:
        raise JetStreamDisabledError()

    async def subscribe_stream(
        self, stream: str, subject: str, durable: str, handler: Handler
    ) -> None:
        raise JetStreamDisabledError()


class JsBus:
    def __init__(self, js: JetStreamContext, cfg: Config, log: Logger | None = None) -> None:
        max_in_flight = cfg.jetstream.max_in_flight
        if max_in_flight <= 0:
            max_in_flight = 100
        ack_wait = cfg.jetstream.ack_wait
        if ack_wait <= 0:
            ack_wait = 30.0
        self._js = js
        self._log = log or nop()
        self._sem = asyncio.Semaphore(max_in_flight)
        self._ack_wait = ack_wait
        self._max_in_flight = max_in_flight
        self._instr = NatsInstruments()
        self._tasks: set[asyncio.Task[None]] = set()

    async def ensure_streams(self, cfg: Config) -> None:
        names = [n.strip() for n in cfg.jetstream.stream_names if n.strip()]
        if not names:
            return
        retention = _parse_retention(cfg.jetstream.retention)
        max_age = cfg.jetstream.max_age if cfg.jetstream.max_age > 0 else 7 * 24 * 3600
        for name in names:
            scfg = api.StreamConfig(
                name=name,
                subjects=[f"{name}.>"],
                retention=retention,
                max_age=max_age,
                storage=api.StorageType.FILE,
            )
            try:
                await self._js.add_stream(config=scfg)
            except Exception as exc:
                raise RuntimeError(f"natskit: ensure stream {name}: {exc}") from exc

    async def publish_stream(self, subject: str, payload: bytes) -> None:
        status = "ok"
        start = time.perf_counter()
        with self._instr.publish_span(subject, payload) as (span, headers):
            try:
                await self._js.publish(subject, payload, headers=headers)
            except Exception as exc:
                status = "error"
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise RuntimeError(f"natskit: jetstream publish: {exc}") from exc
            finally:
                dur = time.perf_counter() - start
                attrs = {"subject": subject, "transport": "jetstream"}
                self._instr.published_total.add(1, {**attrs, "status": status})
                self._instr.publish_duration.record(dur, attrs)

    async def subscribe_stream(
        self, stream: str, subject: str, durable: str, handler: Handler
    ) -> None:
        ccfg = api.ConsumerConfig(
            durable_name=durable,
            ack_policy=api.AckPolicy.EXPLICIT,
            filter_subject=subject,
            ack_wait=self._ack_wait,
            max_ack_pending=self._max_in_flight,
        )

        async def cb(msg: Msg) -> None:
            await self._sem.acquire()
            task = asyncio.create_task(self._handle_one(msg, subject, handler))
            self._tasks.add(task)
            task.add_done_callback(self._on_done)

        await self._js.subscribe(
            subject, stream=stream, durable=durable, cb=cb, manual_ack=True, config=ccfg
        )

    def _on_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        self._sem.release()

    async def _handle_one(self, msg: Msg, subject: str, handler: Handler) -> None:
        attrs = {"subject": subject, "transport": "jetstream"}
        self._instr.inflight.add(1, attrs)
        span = self._instr.consumer_span(subject, msg.data, dict(msg.headers or {}))
        start = time.perf_counter()
        status = "ok"
        try:
            try:
                await handler(msg.data)
            except Exception as exc:
                status = "handler_error"
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                self._log.error(
                    "natskit: jetstream handler error",
                    subject=subject,
                    err=str(exc),
                    stack=traceback.format_exc(),
                )
                await msg.nak()
                return
            await msg.ack()
        finally:
            dur = time.perf_counter() - start
            span.end()
            self._instr.consumed_total.add(1, {**attrs, "status": status})
            self._instr.handle_duration.record(dur, attrs)
            self._instr.inflight.add(-1, attrs)


def _parse_retention(s: str) -> api.RetentionPolicy:
    key = s.strip().lower()
    if key in ("", "limits"):
        return api.RetentionPolicy.LIMITS
    if key == "interest":
        return api.RetentionPolicy.INTEREST
    if key in ("workqueue", "work_queue"):
        return api.RetentionPolicy.WORK_QUEUE
    raise ValueError(f"natskit: unknown retention policy: {s!r}")


async def new_stream_bus(conn: Conn, cfg: Config, log: Logger | None = None) -> StreamBus:
    if not cfg.jetstream.enabled:
        return DisabledStreamBus()
    js = conn.raw.jetstream()
    bus = JsBus(js, cfg, log)
    await bus.ensure_streams(cfg)
    return bus


async def publish_stream_json[T](bus: StreamBus, subject: str, msg: T) -> None:
    data = json.dumps(msg, separators=(",", ":")).encode()
    await bus.publish_stream(subject, data)


async def subscribe_stream_json[T](
    bus: StreamBus, stream: str, subject: str, durable: str, handler: Callable[[T], Awaitable[None]]
) -> None:
    async def wrap(payload: bytes) -> None:
        v: T = json.loads(payload)
        await handler(v)

    await bus.subscribe_stream(stream, subject, durable, wrap)
