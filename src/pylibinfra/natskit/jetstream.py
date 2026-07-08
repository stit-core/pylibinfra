from __future__ import annotations

import asyncio
import json
import time
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from nats.aio.client import Client
from nats.aio.msg import Msg
from nats.js import api
from nats.js.client import JetStreamContext
from nats.js.errors import APIError
from opentelemetry.trace import Span, Status, StatusCode

from pylibinfra.logkit import Logger, nop

from .config import Config
from .conn import Conn
from .context import bind_context, unbind_context
from .instrumentation import NatsInstruments

Handler = Callable[[bytes], Awaitable[None]]

# Код ошибки JetStream «stream name already in use» — сигнал перейти на update_stream.
_STREAM_EXISTS = 10058

# Внутренняя деталь push-консьюмера nats-py: flow-control требует idle_heartbeat.
# В Go pull-консьюмере этих полей нет, поэтому публичный env-контракт их не заводит
# (равняем на golibinfra) — держим фиксированными.
_FLOW_CONTROL = True
_IDLE_HEARTBEAT = 5.0


class JetStreamDisabledError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("natskit: jetstream is disabled")


@dataclass(frozen=True, slots=True)
class StreamMsg:
    """Единица батч-публикации в JetStream. msg_id (опц.) ставит Nats-Msg-Id: в
    пределах окна StreamConfig.duplicate_window сообщения с тем же id
    дедуплицируются стримом (effectively-once персист при ретраях продьюсера)."""

    subject: str
    payload: bytes
    msg_id: str = ""


class StreamBus(Protocol):
    async def publish_stream(
        self, subject: str, payload: bytes, msg_id: str | None = None
    ) -> None: ...
    async def publish_stream_batch(self, msgs: list[StreamMsg]) -> list[Exception | None]: ...
    async def subscribe_stream(
        self, stream: str, subject: str, durable: str, handler: Handler
    ) -> None: ...
    async def drain(self) -> None: ...


class DisabledStreamBus:
    async def publish_stream(self, subject: str, payload: bytes, msg_id: str | None = None) -> None:
        raise JetStreamDisabledError()

    async def publish_stream_batch(self, msgs: list[StreamMsg]) -> list[Exception | None]:
        raise JetStreamDisabledError()

    async def subscribe_stream(
        self, stream: str, subject: str, durable: str, handler: Handler
    ) -> None:
        raise JetStreamDisabledError()

    async def drain(self) -> None:
        return


class JsBus:
    def __init__(
        self, js: JetStreamContext, nc: Client, cfg: Config, log: Logger | None = None
    ) -> None:
        max_in_flight = cfg.jetstream.max_in_flight
        if max_in_flight <= 0:
            max_in_flight = 100
        ack_wait = cfg.jetstream.ack_wait
        if ack_wait <= 0:
            ack_wait = 30.0
        self._js = js
        self._nc = nc
        self._log = log or nop()
        self._sem = asyncio.Semaphore(max_in_flight)
        self._ack_wait = ack_wait
        self._max_in_flight = max_in_flight
        self._max_deliver = cfg.jetstream.max_deliver
        self._nak_backoff = cfg.jetstream.nak_backoff
        self._dlq_subject = cfg.jetstream.dlq_subject
        self._instr = NatsInstruments()
        self._tasks: set[asyncio.Task[None]] = set()
        self._subs: list[JetStreamContext.PushSubscription] = []

    async def ensure_streams(self, cfg: Config) -> None:
        names = [n.strip() for n in cfg.jetstream.stream_names if n.strip()]
        if not names:
            return
        retention = _parse_retention(cfg.jetstream.retention)
        max_age = cfg.jetstream.max_age if cfg.jetstream.max_age > 0 else 7 * 24 * 3600
        dup = cfg.jetstream.duplicates if cfg.jetstream.duplicates > 0 else 120.0
        # JetStream требует duplicate_window ≤ max_age.
        dup = min(dup, max_age)
        for name in names:
            scfg = api.StreamConfig(
                name=name,
                subjects=[f"{name}.>"],
                retention=retention,
                max_age=max_age,
                storage=api.StorageType.FILE,
                duplicate_window=dup,
            )
            try:
                await self._js.add_stream(config=scfg)
            except APIError as exc:
                # Стрим уже есть — идемпотентно обновляем (create-or-update).
                if exc.err_code == _STREAM_EXISTS:
                    await self._js.update_stream(config=scfg)
                    continue
                raise RuntimeError(f"natskit: ensure stream {name}: {exc}") from exc

    async def publish_stream(self, subject: str, payload: bytes, msg_id: str | None = None) -> None:
        span, headers = self._instr.start_publish_span(subject, payload)
        if msg_id:
            headers[api.Header.MSG_ID] = msg_id
        start = time.perf_counter()
        err: Exception | None = None
        try:
            await self._js.publish(subject, payload, headers=headers)
        except Exception as exc:
            err = exc
        self._record_publish(subject, time.perf_counter() - start, err, span)
        if err is not None:
            raise RuntimeError(f"natskit: jetstream publish: {err}") from err

    async def publish_stream_batch(self, msgs: list[StreamMsg]) -> list[Exception | None]:
        # Async-батч: все улетают разом через publish_async, затем ждём PubAck по
        # каждому. results[i] == None — подтверждён персистом; дедуп по msg_id —
        # без дублей. Порядок персиста best-effort (частичный сбой → ретрай).
        if not msgs:
            return []
        results: list[Exception | None] = [None] * len(msgs)
        pending: list[tuple[int, asyncio.Future[api.PubAck], Span, float]] = []
        for i, m in enumerate(msgs):
            span, headers = self._instr.start_publish_span(m.subject, m.payload)
            if m.msg_id:
                headers[api.Header.MSG_ID] = m.msg_id
            start = time.perf_counter()
            try:
                fut = await self._js.publish_async(m.subject, m.payload, headers=headers)
            except Exception as exc:
                results[i] = exc
                self._record_publish(m.subject, time.perf_counter() - start, exc, span)
                continue
            pending.append((i, fut, span, start))

        for i, fut, span, start in pending:
            err: Exception | None = None
            try:
                await fut
            except Exception as exc:
                err = exc
            results[i] = err
            self._record_publish(msgs[i].subject, time.perf_counter() - start, err, span)
        return results

    def _record_publish(self, subject: str, dur: float, err: Exception | None, span: Span) -> None:
        status = "ok"
        if err is not None:
            status = "error"
            span.record_exception(err)
            span.set_status(Status(StatusCode.ERROR, str(err)))
        span.end()
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
            # 0/None — серверный unlimited (backstop); client-side term ниже.
            max_deliver=self._max_deliver if self._max_deliver > 0 else None,
            flow_control=_FLOW_CONTROL,
            idle_heartbeat=_IDLE_HEARTBEAT,
        )

        async def cb(msg: Msg) -> None:
            await self._sem.acquire()
            task = asyncio.create_task(self._handle_one(msg, subject, handler))
            self._tasks.add(task)
            task.add_done_callback(self._on_done)

        sub = await self._js.subscribe(
            subject, stream=stream, durable=durable, cb=cb, manual_ack=True, config=ccfg
        )
        self._subs.append(sub)

    def _on_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        self._sem.release()

    async def _handle_one(self, msg: Msg, subject: str, handler: Handler) -> None:
        attrs = {"subject": subject, "transport": "jetstream"}
        self._instr.inflight.add(1, attrs)
        span = self._instr.consumer_span(subject, msg.data, dict(msg.headers or {}))
        start = time.perf_counter()
        status = "ok"
        tokens = bind_context(subject, msg.headers)
        try:
            handler_err, panicked = await self._invoke(subject, handler, msg.data)
            if handler_err is not None:
                span.record_exception(handler_err)
                span.set_status(Status(StatusCode.ERROR, str(handler_err)))
                status = await self._fail_stream(msg, subject, handler_err, panicked)
            else:
                try:
                    await msg.ack()
                except Exception as exc:
                    self._log.error("natskit: jetstream ack error", subject=subject, err=str(exc))
        finally:
            unbind_context(tokens)
            dur = time.perf_counter() - start
            span.end()
            self._instr.consumed_total.add(1, {**attrs, "status": status})
            self._instr.handle_duration.record(dur, attrs)
            self._instr.inflight.add(-1, attrs)

    async def _invoke(
        self, subject: str, handler: Handler, data: bytes
    ) -> tuple[BaseException | None, bool]:
        # Возврат (err, panicked): panicked=True — не-graceful аварийный выход
        # хендлера (аналог Go recover). CancelledError и фатальные сигналы — наружу.
        try:
            await handler(data)
            return None, False
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return exc, False
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt | SystemExit):
                raise
            self._log.error(
                "natskit: jetstream handler panic",
                subject=subject,
                panic=str(exc),
                stack=traceback.format_exc(),
            )
            return exc, True

    async def _fail_stream(
        self, msg: Msg, subject: str, cause: BaseException, panicked: bool
    ) -> str:
        # На исчерпании max_deliver — DLQ (если задан) + term (стоп ределивери);
        # иначе NAK с опциональным backoff.
        if self._terminal(msg):
            await self._dead_letter(msg, cause)
            try:
                await msg.term()
            except Exception as exc:
                self._log.error("natskit: jetstream term error", subject=subject, err=str(exc))
            return "dead"

        if self._nak_backoff > 0:
            await msg.nak(delay=self._nak_backoff)
        else:
            await msg.nak()
        if not panicked:
            # panic уже залогирован в _invoke.
            self._log.error(
                "natskit: jetstream handler error",
                subject=subject,
                num_delivered=_num_delivered(msg),
                err=str(cause),
            )
        return "panic" if panicked else "handler_error"

    def _terminal(self, msg: Msg) -> bool:
        if self._max_deliver <= 0:
            return False
        return _num_delivered(msg) >= self._max_deliver

    async def _dead_letter(self, msg: Msg, cause: BaseException) -> None:
        if not self._dlq_subject:
            return
        # core-nats publish (не JS): dlq_subject намеренно ВНЕ filter `{name}.>` стрима —
        # иначе term'нутое сообщение вернулось бы в тот же консьюмер и петля восстановилась.
        headers = dict(msg.headers or {})
        headers["Natskit-Dlq-Origin"] = msg.subject
        headers["Natskit-Dlq-Error"] = str(cause)
        try:
            await self._nc.publish(self._dlq_subject, msg.data, headers=headers)
        except Exception as exc:
            self._log.error("natskit: dlq publish", subject=self._dlq_subject, err=str(exc))

    async def drain(self) -> None:
        # порядок: unsubscribe (стоп новых доставок) → дождаться in-flight тасок (ack/nak до
        # закрытия conn) → nc.drain остаётся на закрытии соединения (NatsProvider)
        for sub in self._subs:
            await sub.unsubscribe()
        if not self._tasks:
            self._log.info("natskit: jetstream drained", inflight=0)
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._tasks, return_exceptions=True), timeout=self._ack_wait
            )
        except TimeoutError:
            self._log.error("natskit: jetstream drain timeout", inflight=len(self._tasks))
            return
        self._log.info("natskit: jetstream drained", inflight=0)


def _num_delivered(msg: Msg) -> int:
    try:
        return int(msg.metadata.num_delivered)
    except Exception:
        return 0


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
    bus = JsBus(js, conn.raw, cfg, log)
    await bus.ensure_streams(cfg)
    return bus


async def publish_stream_json[T](
    bus: StreamBus, subject: str, msg: T, msg_id: str | None = None
) -> None:
    data = json.dumps(msg, separators=(",", ":")).encode()
    await bus.publish_stream(subject, data, msg_id)


async def subscribe_stream_json[T](
    bus: StreamBus, stream: str, subject: str, durable: str, handler: Callable[[T], Awaitable[None]]
) -> None:
    async def wrap(payload: bytes) -> None:
        v: T = json.loads(payload)
        await handler(v)

    await bus.subscribe_stream(stream, subject, durable, wrap)
