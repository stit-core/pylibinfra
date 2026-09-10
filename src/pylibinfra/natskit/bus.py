"""Core NATS: публикация без персиста, fan-out и queue-подписки, backpressure и deadline."""

from __future__ import annotations

import asyncio
import json
import time
import traceback
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

from nats.aio.msg import Msg
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from pylibinfra.logkit import Logger

from .config import DEFAULT_MAX_IN_FLIGHT, Config, positive_int_or
from .conn import Conn
from .context import message_context
from .errors import PublishError, SubscribeError, TerminalError
from .instrumentation import NatsInstruments
from .publish import new_publish_spec
from .subscription import Handler, Stop, Subscription, TrackedSubscription


class Bus(Protocol):
    """Core NATS: fire-and-forget публикация, fan-out и load-balanced подписки."""

    async def publish(
        self,
        subject: str,
        payload: bytes,
        *,
        msg_id: str | None = None,
        headers: Mapping[str, str] | None = None,
        metric_subject: str | None = None,
    ) -> None:
        """Публикует байты; `metric_subject` — шаблон label'а `subject` publish-метрик."""

    async def subscribe(self, subject: str, handler: Handler) -> Subscription:
        """Fan-out подписка: копию сообщения получает каждый инстанс сервиса."""

    async def subscribe_queue(self, subject: str, queue: str, handler: Handler) -> Subscription:
        """Queue-подписка: сообщение получает один инстанс группы (масштабирование консьюмеров)."""


class NatsBus:
    """`Bus` поверх соединения: семафор `NATS_MAX_IN_FLIGHT` и deadline `NATS_HANDLER_TIMEOUT`."""

    def __init__(self, conn: Conn, cfg: Config | None = None, log: Logger | None = None) -> None:
        cfg = cfg or Config()
        self._conn = conn
        self._nc = conn.raw
        self._log = log.with_(instance=conn.instance) if log is not None else conn.log
        self._sem = asyncio.Semaphore(
            positive_int_or(cfg.backpressure.max_in_flight, DEFAULT_MAX_IN_FLIGHT)
        )
        self._handler_timeout = cfg.handler_timeout
        self._instr = NatsInstruments(conn.instance)

    async def publish(
        self,
        subject: str,
        payload: bytes,
        *,
        msg_id: str | None = None,
        headers: Mapping[str, str] | None = None,
        metric_subject: str | None = None,
    ) -> None:
        """Публикует байты в Core NATS; ошибка транспорта — `PublishError`."""
        spec = new_publish_spec(msg_id=msg_id, headers=headers, metric_subject=metric_subject)
        span, wire = self._instr.start_publish_span(subject, payload, spec)
        start = time.perf_counter()
        err: BaseException | None = None
        try:
            await self._nc.publish(subject, payload, headers=wire)
        except Exception as exc:
            err = exc
        self._instr.record_publish(spec, time.perf_counter() - start, err, span)
        if err is not None:
            raise PublishError(f"natskit: publish: {err}") from err

    async def subscribe(self, subject: str, handler: Handler) -> Subscription:
        """Fan-out подписка на subject (wildcard'ы NATS поддерживаются)."""
        return await self._subscribe(subject, "", handler)

    async def subscribe_queue(self, subject: str, queue: str, handler: Handler) -> Subscription:
        """Подписка queue-группы: сообщение достаётся одному её участнику."""
        return await self._subscribe(subject, queue, handler)

    async def _subscribe(self, subject: str, queue: str, handler: Handler) -> Subscription:
        async def start(sub: TrackedSubscription) -> Stop:
            async def on_message(msg: Msg) -> None:
                # Блокировка delivery-цикла на семафоре и есть backpressure.
                await self._sem.acquire()
                sub.spawn(self._handle_one(msg, subject, handler), self._sem.release)

            try:
                nats_sub = await self._nc.subscribe(subject, queue=queue, cb=on_message)
            except Exception as exc:
                raise SubscribeError(f"natskit: subscribe {subject}: {exc}") from exc

            async def stop(drain: bool, budget: float | None) -> None:
                if not drain:
                    await nats_sub.unsubscribe()
                    return
                try:
                    # nats-py дренит буфер без бюджета, а delivery-цикл на исчерпанном
                    # семафоре не вернётся, пока не освободится слот.
                    await asyncio.wait_for(nats_sub.drain(), budget)  # type: ignore[no-untyped-call]
                except TimeoutError as exc:
                    raise TimeoutError(f"natskit: subscription drain timeout on {subject}") from exc

            return stop

        return await self._conn.register(subject, start)

    async def _handle_one(self, msg: Msg, filter_: str, handler: Handler) -> None:
        span = self._instr.start_consumer_span(msg.subject, filter_, msg.data, msg.headers)
        ctx = trace.set_span_in_context(span)
        attrs = self._instr.subject_attrs(filter_)
        self._instr.inflight.add(1, attrs, context=ctx)
        start = time.perf_counter()
        status = "ok"
        try:
            status = await self._invoke(msg, filter_, handler, span)
        finally:
            self._instr.consumed_total.add(
                1, self._instr.subject_attrs(filter_, status=status), context=ctx
            )
            self._instr.handle_duration.record(time.perf_counter() - start, attrs, context=ctx)
            self._instr.inflight.add(-1, attrs, context=ctx)
            span.end()

    async def _invoke(self, msg: Msg, filter_: str, handler: Handler, span: Span) -> str:
        try:
            with message_context(msg.subject, msg.headers):
                await invoke_handler(handler, msg.data, self._handler_timeout)
        except asyncio.CancelledError:
            # Отмена приходит от close(): бюджет drain исчерпан, логировать нечего.
            _record_error(span, "natskit: handler cancelled")
            raise
        except Exception as exc:
            _record_error(span, str(exc))
            self._log.error("natskit: handler error", subject=filter_, err=str(exc))
            return "handler_error"
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt | SystemExit):
                raise
            _record_error(span, str(exc))
            self._log.error(
                "natskit: handler panic",
                subject=filter_,
                panic=str(exc),
                stack=traceback.format_exc(),
            )
            return "panic"
        return "ok"


async def invoke_handler(handler: Handler, payload: bytes, budget: float) -> None:
    """Вызывает обработчик с дедлайном (`budget <= 0` — без него); исчерпан — `TimeoutError`."""
    if budget <= 0:
        await handler(payload)
        return
    try:
        await asyncio.wait_for(handler(payload), budget)
    except TimeoutError as exc:
        raise TimeoutError(f"natskit: handler timeout after {budget:g}s") from exc


def _record_error(span: Span, message: str) -> None:
    span.set_status(Status(StatusCode.ERROR, message))


def new_bus(conn: Conn, cfg: Config | None = None, log: Logger | None = None) -> Bus:
    """Собирает Core-шину поверх соединения; обычный путь — `Client.bus`."""
    return NatsBus(conn, cfg, log)


def encode_json(msg: object) -> bytes:
    """Сериализует payload в компактный JSON; несериализуемое значение — `PublishError`."""
    try:
        return json.dumps(msg, separators=(",", ":")).encode()
    except (TypeError, ValueError) as exc:
        raise PublishError(f"natskit: marshal payload: {exc}") from exc


def decode_json[T](handler: Callable[[T], Awaitable[None]]) -> Handler:
    """Оборачивает типизированный обработчик; битый payload — `TerminalError` (DLQ, не ретрай)."""

    async def wrapped(payload: bytes) -> None:
        try:
            value: T = json.loads(payload)
        except ValueError as exc:
            raise TerminalError(f"natskit: unmarshal payload: {exc}") from exc
        await handler(value)

    return wrapped


async def publish_json[T](
    bus: Bus,
    subject: str,
    msg: T,
    *,
    msg_id: str | None = None,
    headers: Mapping[str, str] | None = None,
    metric_subject: str | None = None,
) -> None:
    """Маршалит `msg` в JSON и публикует его в Core NATS."""
    await bus.publish(
        subject,
        encode_json(msg),
        msg_id=msg_id,
        headers=headers,
        metric_subject=metric_subject,
    )


async def subscribe_json[T](
    bus: Bus, subject: str, handler: Callable[[T], Awaitable[None]]
) -> Subscription:
    """Fan-out подписка с декодом payload в `T`."""
    return await bus.subscribe(subject, decode_json(handler))


async def subscribe_queue_json[T](
    bus: Bus, subject: str, queue: str, handler: Callable[[T], Awaitable[None]]
) -> Subscription:
    """Queue-подписка с декодом payload в `T`."""
    return await bus.subscribe_queue(subject, queue, decode_json(handler))
