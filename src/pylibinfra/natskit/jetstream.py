"""JetStream: персист, дедуп по `Nats-Msg-Id` и durable-консьюмеры с poison-обработкой.

Неуспешное сообщение возвращается `nak` с backoff; терминальная ошибка обработчика
(`TerminalError`) или исчерпанный `NATS_JS_MAX_DELIVER` уводят его в DLQ и `term` —
причём `term` делается только после подтверждённой DLQ-публикации.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import traceback
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from nats.aio.client import Client
from nats.aio.msg import Msg
from nats.js import api
from nats.js.client import JetStreamContext
from nats.js.errors import APIError, NoStreamResponseError, NotFoundError
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from pylibinfra.logkit import Logger

from .bus import decode_json, encode_json, invoke_handler
from .config import (
    DEFAULT_ACK_WAIT,
    DEFAULT_DUPLICATES,
    DEFAULT_MAX_AGE,
    DEFAULT_MAX_IN_FLIGHT,
    DEFAULT_PUBLISH_ASYNC_TIMEOUT,
    Config,
    positive_int_or,
    positive_or,
)
from .conn import Conn
from .context import message_context
from .errors import (
    ConfigError,
    JetStreamDisabledError,
    PublishError,
    StreamError,
    is_terminal,
)
from .instrumentation import TRANSPORT_JETSTREAM, NatsInstruments
from .publish import MSG_ID_HEADER, PublishSpec, StreamMsg, new_publish_spec
from .subjects import subject_matches
from .subscription import Handler, Stop, Subscription, TrackedSubscription

HEADER_DLQ_ORIGIN = "Natskit-Dlq-Origin"
HEADER_DLQ_ERROR = "Natskit-Dlq-Error"
HEADER_DLQ_MSG_ID = "Natskit-Dlq-Msg-Id"
HEADER_DLQ_STREAM = "Natskit-Dlq-Stream"
HEADER_DLQ_CONSUMER = "Natskit-Dlq-Consumer"
HEADER_DLQ_DELIVERIES = "Natskit-Dlq-Deliveries"
HEADER_DLQ_REASON = "Natskit-Dlq-Reason"
HEADER_DLQ_INSTANCE = "Natskit-Dlq-Instance"

REASON_MAX_DELIVER = "max-deliver"
REASON_TERMINAL = "terminal"

# Код JetStream «stream name already in use»: гонка создания, не ошибка конфигурации.
_STREAM_NAME_IN_USE = 10058

_DLQ_PUBLISH_TIMEOUT = 5.0
_MAX_DLQ_ERROR_LEN = 200
# Период опроса pull-консьюмера: он же верхняя граница задержки drain'а подписки.
_FETCH_TIMEOUT = 1.0
_MAX_FETCH_BATCH = 64
# Тикер с нулевым периодом бесполезен, слишком частый заваливает сервер.
_MIN_IN_PROGRESS_INTERVAL = 0.1
_CONSUME_ERROR_BACKOFF = 1.0
_EXPECTED_HEADER_PREFIX = "Nats-Expected-"


@dataclass(frozen=True, slots=True)
class StreamConsumer:
    """Координаты консьюмера: едут в label'ы, спаны и заголовки DLQ-копии."""

    stream: str
    durable: str
    filter: str


class StreamBus(Protocol):
    """JetStream: публикация с подтверждением персиста и durable-подписки."""

    async def publish_stream(
        self,
        subject: str,
        payload: bytes,
        *,
        msg_id: str | None = None,
        headers: Mapping[str, str] | None = None,
        metric_subject: str | None = None,
    ) -> None:
        """Публикует сообщение и ждёт `PubAck`; сбой — `PublishError`."""

    async def publish_stream_batch(self, msgs: Sequence[StreamMsg]) -> list[Exception | None]:
        """Публикует батч async'ом и ждёт `PubAck` каждого; `results[i] is None` — подтверждён."""

    async def subscribe_stream(
        self, stream: str, subject: str, durable: str, handler: Handler
    ) -> Subscription:
        """Durable-подписка с explicit-ack и poison-обработкой."""


class DisabledStreamBus:
    """Заглушка при `NATS_JETSTREAM_ENABLED=false`: методы отдают `JetStreamDisabledError`.

    Заглушка, а не `None`: DI-граф собирается, а неверное использование выключенного
    транспорта — явная ошибка в рантайме, а не падение по `None`.
    """

    async def publish_stream(
        self,
        subject: str,
        payload: bytes,
        *,
        msg_id: str | None = None,
        headers: Mapping[str, str] | None = None,
        metric_subject: str | None = None,
    ) -> None:
        """Всегда `JetStreamDisabledError`."""
        raise JetStreamDisabledError

    async def publish_stream_batch(self, msgs: Sequence[StreamMsg]) -> list[Exception | None]:
        """Всегда `JetStreamDisabledError`."""
        raise JetStreamDisabledError

    async def subscribe_stream(
        self, stream: str, subject: str, durable: str, handler: Handler
    ) -> Subscription:
        """Всегда `JetStreamDisabledError`."""
        raise JetStreamDisabledError


class JsBus:
    """`StreamBus` поверх JetStream-контекста одного соединения."""

    def __init__(
        self, js: JetStreamContext, conn: Conn, cfg: Config, log: Logger | None = None
    ) -> None:
        self._js = js
        self._conn = conn
        self._nc: Client = conn.raw
        self._log = log.with_(instance=conn.instance) if log is not None else conn.log
        js_cfg = cfg.jetstream
        self._max_in_flight = positive_int_or(js_cfg.max_in_flight, DEFAULT_MAX_IN_FLIGHT)
        self._sem = asyncio.Semaphore(self._max_in_flight)
        self._ack_wait = positive_or(js_cfg.ack_wait, DEFAULT_ACK_WAIT)
        self._handler_timeout = positive_or(js_cfg.handler_timeout, self._ack_wait)
        self._publish_timeout = positive_or(
            js_cfg.publish_async_timeout, DEFAULT_PUBLISH_ASYNC_TIMEOUT
        )
        self._max_deliver = js_cfg.max_deliver
        self._nak_backoff = js_cfg.nak_backoff
        self._dlq_subject = js_cfg.dlq_subject
        self._dlq_in_stream = False
        self._instr = NatsInstruments(conn.instance, TRANSPORT_JETSTREAM)

    async def ensure_streams(self, cfg: Config) -> None:
        """Декларирует стримы `NATS_JS_STREAM_NAMES` по режиму `NATS_JS_STREAM_MANAGED`."""
        names = [name.strip() for name in cfg.jetstream.stream_names if name.strip()]
        if not names:
            return
        if not cfg.jetstream.stream_managed:
            for name in names:
                try:
                    await self._js.stream_info(name)
                except Exception as exc:
                    raise StreamError(
                        f"natskit: stream {name} is unmanaged and unavailable: {exc}"
                    ) from exc
            return

        max_age = positive_or(cfg.jetstream.max_age, DEFAULT_MAX_AGE)
        # JetStream требует окно дедупа <= срока хранения.
        duplicates = min(positive_or(cfg.jetstream.duplicates, DEFAULT_DUPLICATES), max_age)
        for name in names:
            want = api.StreamConfig(
                name=name,
                subjects=[f"{name}.>"],
                retention=parse_retention(cfg.jetstream.retention),
                max_age=max_age,
                storage=api.StorageType.FILE,
                duplicate_window=duplicates,
            )
            await self._reconcile_stream(want)

    async def _reconcile_stream(self, want: api.StreamConfig) -> None:
        """Создаёт отсутствующий стрим целиком, в существующем доливает только свой subject."""
        name = str(want.name)
        current = await self._stream_config(want)
        if current is None:
            return
        self._warn_drift(want, current)
        have = list(current.subjects or [])
        merged = have + [s for s in (want.subjects or []) if s not in have]
        if len(merged) == len(have):
            return
        current.subjects = merged
        try:
            await self._js.update_stream(config=current)
        except Exception as exc:
            raise StreamError(f"natskit: update stream {name} subjects: {exc}") from exc

    async def _stream_config(self, want: api.StreamConfig) -> api.StreamConfig | None:
        name = str(want.name)
        try:
            info = await self._js.stream_info(name)
        except NotFoundError:
            try:
                await self._js.add_stream(config=want)
            except APIError as exc:
                # Гонка двух инстансов на создании — сверяем subjects как у существующего.
                if exc.err_code != _STREAM_NAME_IN_USE:
                    raise StreamError(f"natskit: create stream {name}: {exc}") from exc
                info = await self._js.stream_info(name)
                return info.config
            except Exception as exc:
                raise StreamError(f"natskit: create stream {name}: {exc}") from exc
            return None
        except Exception as exc:
            raise StreamError(f"natskit: stream {name}: {exc}") from exc
        return info.config

    def _warn_drift(self, want: api.StreamConfig, have: api.StreamConfig) -> None:
        """Существующий стрим кит не переписывает — расхождение с ENV сообщается в лог."""
        for field, wanted, actual in (
            ("retention", want.retention, have.retention),
            ("max_age", want.max_age, have.max_age),
            ("duplicates", want.duplicate_window, have.duplicate_window),
            ("storage", want.storage, have.storage),
        ):
            if wanted != actual:
                self._log.warn(
                    "natskit: stream config drift",
                    stream=want.name,
                    field=field,
                    want=str(wanted),
                    have=str(actual),
                )

    async def detect_dlq_stream(self) -> None:
        """Выясняет, накрыт ли DLQ-subject стримом: от этого зависит способ публикации копии."""
        if not self._dlq_subject:
            return
        try:
            stream = await self._js.find_stream_name_by_subject(self._dlq_subject)
        except NotFoundError:
            return
        except Exception as exc:
            self._log.warn("natskit: dlq stream lookup", subject=self._dlq_subject, err=str(exc))
            return
        self._dlq_in_stream = True
        self._log.info(
            "natskit: dlq subject is stored in a stream", subject=self._dlq_subject, stream=stream
        )

    async def publish_stream(
        self,
        subject: str,
        payload: bytes,
        *,
        msg_id: str | None = None,
        headers: Mapping[str, str] | None = None,
        metric_subject: str | None = None,
    ) -> None:
        """Публикует сообщение и ждёт `PubAck` в пределах `NATS_JS_PUBLISH_ASYNC_TIMEOUT`."""
        spec = new_publish_spec(msg_id=msg_id, headers=headers, metric_subject=metric_subject)
        span, wire = self._instr.start_publish_span(subject, payload, spec)
        start = time.perf_counter()
        err: BaseException | None = None
        try:
            await self._js.publish(subject, payload, headers=wire, timeout=self._publish_timeout)
        except Exception as exc:
            err = exc
        self._instr.record_publish(spec, time.perf_counter() - start, err, span)
        if err is not None:
            raise PublishError(f"natskit: jetstream publish: {err}") from err

    async def publish_stream_batch(self, msgs: Sequence[StreamMsg]) -> list[Exception | None]:
        """Публикует батч async'ом и ждёт `PubAck` каждого своего сообщения.

        Ожидание идёт по своим future'ам: конкурентные батчи одной шины не сцеплены,
        а потерянный `PubAck` завершается ошибкой по `NATS_JS_PUBLISH_ASYNC_TIMEOUT`.
        """
        if not msgs:
            return []
        results: list[Exception | None] = [None] * len(msgs)
        pending: list[tuple[int, asyncio.Future[api.PubAck], Span, float, PublishSpec]] = []
        for i, msg in enumerate(msgs):
            spec = msg.spec()
            span, wire = self._instr.start_publish_span(msg.subject, msg.payload, spec)
            start = time.perf_counter()
            try:
                future = await self._js.publish_async(msg.subject, msg.payload, headers=wire)
            except Exception as exc:
                results[i] = exc
                self._instr.record_publish(spec, time.perf_counter() - start, exc, span)
                continue
            pending.append((i, future, span, start, spec))

        deadline = asyncio.get_running_loop().time() + self._publish_timeout
        for i, future, span, start, spec in pending:
            err = await self._settle(future, deadline)
            results[i] = err
            self._instr.record_publish(spec, time.perf_counter() - start, err, span)
        return results

    async def _settle(
        self, future: asyncio.Future[api.PubAck], deadline: float
    ) -> Exception | None:
        """Исход одной async-публикации; future не отменяется — поздний ack просто отбрасывается."""
        if not future.done():
            left = max(deadline - asyncio.get_running_loop().time(), 0.0)
            await asyncio.wait({future}, timeout=left)
        if not future.done():
            future.add_done_callback(_discard)
            return PublishError(
                f"natskit: jetstream publish: pub ack timeout after {self._publish_timeout:g}s"
            )
        if future.cancelled():
            return PublishError("natskit: jetstream publish: pub ack cancelled")
        err = future.exception()
        if err is None or isinstance(err, Exception):
            return err
        return PublishError(f"natskit: jetstream publish: {err}")

    async def subscribe_stream(
        self, stream: str, subject: str, durable: str, handler: Handler
    ) -> Subscription:
        """Создаёт (или обновляет) durable pull-консьюмера и запускает его вычитку."""
        if self._dlq_subject and subject_matches(subject, self._dlq_subject):
            raise ConfigError(
                f"natskit: invalid config: NATS_JS_DLQ_SUBJECT {self._dlq_subject!r} is matched "
                f"by consumer filter {subject!r}: dead letters would loop back to the handler"
            )
        consumer = api.ConsumerConfig(
            name=durable,
            durable_name=durable,
            ack_policy=api.AckPolicy.EXPLICIT,
            filter_subject=subject,
            ack_wait=self._ack_wait,
            max_ack_pending=self._max_in_flight,
            # 0 — серверный unlimited; клиентский term ниже всё равно ограничен MaxDeliver.
            max_deliver=self._max_deliver if self._max_deliver > 0 else None,
        )
        sc = StreamConsumer(stream=stream, durable=durable, filter=subject)

        async def start(sub: TrackedSubscription) -> Stop:
            # Внутри register: у инстанса, начавшего close, не появится ни консьюмера,
            # ни висящей inbox-подписки под него.
            try:
                await self._js.add_consumer(stream, config=consumer)
                psub = await self._js.pull_subscribe_bind(consumer=durable, stream=stream)
            except Exception as exc:
                raise StreamError(f"natskit: jetstream consumer: {exc}") from exc
            stopping = asyncio.Event()
            loop_task = asyncio.create_task(
                self._consume(psub, sub, sc, handler, stopping), name=f"natskit.consume.{durable}"
            )

            async def stop(drain: bool, budget: float | None) -> None:
                stopping.set()
                drained = True
                if drain:
                    done, _ = await asyncio.wait({loop_task}, timeout=_stop_budget(budget))
                    drained = bool(done)
                loop_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await loop_task
                with contextlib.suppress(Exception):
                    await psub.unsubscribe()
                if not drained:
                    raise TimeoutError(f"natskit: subscription drain timeout on {subject}")

            return stop

        return await self._conn.register(subject, start)

    async def _consume(
        self,
        psub: JetStreamContext.PullSubscription,
        sub: TrackedSubscription,
        sc: StreamConsumer,
        handler: Handler,
        stopping: asyncio.Event,
    ) -> None:
        batch = max(1, min(self._max_in_flight, _MAX_FETCH_BATCH))
        while not stopping.is_set():
            try:
                msgs = await psub.fetch(batch=batch, timeout=_FETCH_TIMEOUT)
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                continue
            except Exception as exc:
                self._log.warn(
                    "natskit: jetstream consume error",
                    stream=sc.stream,
                    subject=sc.filter,
                    durable=sc.durable,
                    err=str(exc),
                )
                if self._nc.is_closed:
                    return
                await asyncio.sleep(_CONSUME_ERROR_BACKOFF)
                continue
            for msg in msgs:
                await self._sem.acquire()
                sub.spawn(self._handle_one(sc, msg, handler), self._sem.release)

    async def _handle_one(self, sc: StreamConsumer, msg: Msg, handler: Handler) -> None:
        subject = msg.subject
        span = self._instr.start_consumer_span(subject, sc.filter, msg.data, msg.headers)
        ctx = trace.set_span_in_context(span)
        attrs = self._instr.subject_attrs(sc.filter)
        self._instr.inflight.add(1, attrs, context=ctx)
        start = time.perf_counter()
        status = "ok"
        keepalive = asyncio.create_task(self._keep_in_progress(msg, subject))
        try:
            panicked, err = await self._invoke(subject, handler, msg)
            keepalive.cancel()
            if err is not None:
                span.set_status(Status(StatusCode.ERROR, str(err)))
                status = await self._fail_stream(sc, msg, err, panicked)
            else:
                await self._ack(msg, subject)
        finally:
            keepalive.cancel()
            self._instr.consumed_total.add(
                1, self._instr.subject_attrs(sc.filter, status=status), context=ctx
            )
            self._instr.handle_duration.record(time.perf_counter() - start, attrs, context=ctx)
            self._instr.inflight.add(-1, attrs, context=ctx)
            span.end()

    async def _invoke(
        self, subject: str, handler: Handler, msg: Msg
    ) -> tuple[bool, BaseException | None]:
        """Исход обработчика: `(panicked, err)`; отмена и фатальные сигналы уходят наружу."""
        try:
            with message_context(subject, msg.headers):
                await invoke_handler(handler, msg.data, self._handler_timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return False, exc
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt | SystemExit):
                raise
            self._log.error(
                "natskit: jetstream handler panic",
                subject=subject,
                panic=str(exc),
                stack=traceback.format_exc(),
            )
            return True, exc
        return False, None

    async def _keep_in_progress(self, msg: Msg, subject: str) -> None:
        """Шлёт `+WPI` каждые AckWait/2: сервер не переотправляет живому, но медленному."""
        interval = max(self._ack_wait / 2, _MIN_IN_PROGRESS_INTERVAL)
        warned = False
        while True:
            await asyncio.sleep(interval)
            try:
                await msg.in_progress()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Транзиентная ошибка не выключает keepalive: иначе ределивери придёт живому.
                if not warned:
                    warned = True
                    self._log.warn("natskit: jetstream in-progress", subject=subject, err=str(exc))

    async def _ack(self, msg: Msg, subject: str) -> None:
        try:
            await msg.ack()
        except Exception as exc:
            self._log.error("natskit: jetstream ack error", subject=subject, err=str(exc))

    async def _fail_stream(
        self, sc: StreamConsumer, msg: Msg, cause: BaseException, panicked: bool
    ) -> str:
        """Судьба неуспешного сообщения: DLQ+term на терминальной ошибке, иначе nak с backoff."""
        subject = msg.subject
        deliveries = _deliveries(msg)
        reason = self._terminal_reason(cause, deliveries)
        if reason:
            try:
                await self._dead_letter(sc, msg, cause, reason, deliveries)
            except Exception as exc:
                # Оригинал не терминируем: недоступный DLQ иначе стирал бы сообщение молча.
                self._log.error(
                    "natskit: jetstream dlq publish failed, message kept for redelivery",
                    stream=sc.stream,
                    durable=sc.durable,
                    subject=subject,
                    deliveries=deliveries,
                    reason=reason,
                    dlq=self._dlq_subject,
                    err=str(exc),
                    cause=str(cause),
                )
                await self._nak(msg, subject)
                return "dlq_error"
            self._log.error(
                "natskit: jetstream message dead",
                stream=sc.stream,
                durable=sc.durable,
                subject=subject,
                deliveries=deliveries,
                reason=reason,
                dlq=self._dlq_subject,
                err=str(cause),
            )
            try:
                await msg.term()
            except Exception as exc:
                self._log.error("natskit: jetstream term error", subject=subject, err=str(exc))
            return "dead"

        await self._nak(msg, subject)
        if not panicked:
            self._log.error("natskit: jetstream handler error", subject=subject, err=str(cause))
        return "panic" if panicked else "handler_error"

    async def _nak(self, msg: Msg, subject: str) -> None:
        try:
            if self._nak_backoff > 0:
                await msg.nak(delay=self._nak_backoff)
            else:
                await msg.nak()
        except Exception as exc:
            # Сервер всё равно переотправит сообщение по AckWait, но сбой не должен теряться.
            self._log.warn("natskit: jetstream nak error", subject=subject, err=str(exc))

    def _terminal_reason(self, cause: BaseException, deliveries: int) -> str:
        if is_terminal(cause):
            return REASON_TERMINAL
        if self._max_deliver > 0 and deliveries >= self._max_deliver:
            return REASON_MAX_DELIVER
        return ""

    async def _dead_letter(
        self, sc: StreamConsumer, msg: Msg, cause: BaseException, reason: str, deliveries: int
    ) -> None:
        """Публикует копию мёртвого сообщения в DLQ; ошибка означает, что копии нет."""
        if not self._dlq_subject:
            return
        headers = self._dlq_headers(sc, msg, cause, reason, deliveries)
        if self._dlq_in_stream:
            try:
                await self._js.publish(
                    self._dlq_subject, msg.data, headers=headers, timeout=_DLQ_PUBLISH_TIMEOUT
                )
                return
            except NoStreamResponseError:
                # Стрим мог исчезнуть после старта — копия всё равно уходит, но без PubAck.
                pass
        await self._nc.publish(self._dlq_subject, msg.data, headers=headers)
        await self._nc.flush(timeout=int(_DLQ_PUBLISH_TIMEOUT))

    def _dlq_headers(
        self, sc: StreamConsumer, msg: Msg, cause: BaseException, reason: str, deliveries: int
    ) -> dict[str, str]:
        headers = {
            key: value
            for key, value in (msg.headers or {}).items()
            # Nats-Expected-* адресованы исходному стриму: в DLQ они молча отвергнут копию.
            if not key.startswith(_EXPECTED_HEADER_PREFIX)
        }
        # С Nats-Msg-Id оригинала стрим с окном дедупа счёл бы копию дублем и выбросил.
        msg_id = headers.pop(MSG_ID_HEADER, "")
        if msg_id:
            headers[HEADER_DLQ_MSG_ID] = msg_id
        headers[HEADER_DLQ_ORIGIN] = msg.subject
        headers[HEADER_DLQ_ERROR] = _header_value(str(cause))[:_MAX_DLQ_ERROR_LEN]
        headers[HEADER_DLQ_REASON] = reason
        headers[HEADER_DLQ_STREAM] = sc.stream
        headers[HEADER_DLQ_CONSUMER] = sc.durable
        headers[HEADER_DLQ_DELIVERIES] = str(deliveries)
        headers[HEADER_DLQ_INSTANCE] = self._conn.instance
        return headers


def _stop_budget(budget: float | None) -> float:
    """Бюджет ожидания pull-цикла: он сверяется со стопом раз в `_FETCH_TIMEOUT`."""
    ceiling = _FETCH_TIMEOUT * 2
    return ceiling if budget is None else min(max(budget, 0.0), ceiling)


def _discard(future: asyncio.Future[Any]) -> None:
    """Забирает поздний результат future'а, чтобы asyncio не ругался на «never retrieved»."""
    if not future.cancelled():
        future.exception()


def _header_value(text: str) -> str:
    """Убирает переводы строк: они ломают header-блок протокола NATS."""
    return text.replace("\r", " ").replace("\n", " ")


def _deliveries(msg: Msg) -> int:
    try:
        return int(msg.metadata.num_delivered or 0)
    except Exception:
        return 0


def parse_retention(value: str) -> api.RetentionPolicy:
    """Политика удержания стрима по значению `NATS_JS_RETENTION`."""
    key = value.strip().lower()
    if key in ("", "limits"):
        return api.RetentionPolicy.LIMITS
    if key == "interest":
        return api.RetentionPolicy.INTEREST
    if key in ("workqueue", "work_queue"):
        return api.RetentionPolicy.WORK_QUEUE
    raise ConfigError(f"natskit: invalid config: NATS_JS_RETENTION: unknown policy {value!r}")


async def new_stream_bus(conn: Conn, cfg: Config, log: Logger | None = None) -> StreamBus:
    """Собирает JetStream-шину и декларирует стримы; при выключенном JetStream — заглушка."""
    if not cfg.jetstream.enabled:
        return DisabledStreamBus()
    js = conn.raw.jetstream()
    bus = JsBus(js, conn, cfg, log)
    await bus.ensure_streams(cfg)
    await bus.detect_dlq_stream()
    return bus


async def publish_stream_json[T](
    bus: StreamBus,
    subject: str,
    msg: T,
    *,
    msg_id: str | None = None,
    headers: Mapping[str, str] | None = None,
    metric_subject: str | None = None,
) -> None:
    """Маршалит `msg` в JSON и публикует его в стрим."""
    await bus.publish_stream(
        subject,
        encode_json(msg),
        msg_id=msg_id,
        headers=headers,
        metric_subject=metric_subject,
    )


async def subscribe_stream_json[T](
    bus: StreamBus,
    stream: str,
    subject: str,
    durable: str,
    handler: Callable[[T], Awaitable[None]],
) -> Subscription:
    """Durable-подписка с декодом payload в `T`; ошибка декода терминальна (DLQ вместо ретраев)."""
    return await bus.subscribe_stream(stream, subject, durable, decode_json(handler))
