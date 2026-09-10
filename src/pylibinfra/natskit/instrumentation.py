"""Метрики и спаны кита плюс перенос W3C trace-context в заголовках сообщения.

Инструменты создаются на инстанс шины (`instance`) и транспорт (`transport=jetstream`
у JetStream-пути), поэтому сигналы двух шин одного процесса не сливаются в одну серию.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from opentelemetry import propagate, trace
from opentelemetry.context import Context
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from pylibinfra._internal import otelkit

from .publish import PublishSpec

_KIT = "natskit"
TRANSPORT_JETSTREAM = "jetstream"

_MESSAGING_SYSTEM = "messaging.system"
_MESSAGING_DESTINATION = "messaging.destination.name"
_MESSAGING_TEMPLATE = "messaging.destination.template"
_MESSAGING_BODY_SIZE = "messaging.message.body.size"
_MESSAGING_OPERATION = "messaging.operation.type"

Attrs = dict[str, Any]


class NatsInstruments:
    """Пять инструментов `nats_*` и producer/consumer-спаны одного инстанса шины."""

    def __init__(self, instance: str, transport: str = "") -> None:
        meter = otelkit.meter(_KIT)
        self.tracer = otelkit.tracer(_KIT)
        self.instance = instance
        self.transport = transport
        self.published_total = meter.create_counter(
            "nats_messages_published_total", description="NATS messages published"
        )
        self.publish_duration = otelkit.duration_histogram(
            meter, "nats_publish_duration_seconds", "Time to publish a NATS message"
        )
        self.consumed_total = meter.create_counter(
            "nats_messages_consumed_total", description="NATS messages consumed"
        )
        self.handle_duration = otelkit.duration_histogram(
            meter, "nats_handle_duration_seconds", "Subscriber handler duration"
        )
        self.inflight = meter.create_up_down_counter(
            "nats_inflight_messages",
            description="Messages currently being handled (per subscription subject)",
        )

    def attrs(self, **extra: Any) -> Attrs:
        """Базовые label'ы измерения: инстанс, транспорт и переданные сверху."""
        base: Attrs = {"instance": self.instance}
        if self.transport:
            base["transport"] = self.transport
        base.update(extra)
        return base

    def subject_attrs(self, filter_: str, **extra: Any) -> Attrs:
        """Label'ы подписки: subject фильтра, а не реальный subject сообщения."""
        return self.attrs(subject=filter_, **extra)

    def start_publish_span(
        self, subject: str, payload: bytes, spec: PublishSpec
    ) -> tuple[Span, dict[str, str]]:
        """Producer-спан (не current) плюс заголовки сообщения с инжектнутым trace-context.

        Спан не делается текущим: конкурентные публикации батча иначе воровали бы
        друг у друга контекст. Вызывающий обязан закрыть спан через `record_publish`.
        """
        name = spec.metric_subject or subject
        attributes: Attrs = {
            _MESSAGING_SYSTEM: "nats",
            _MESSAGING_DESTINATION: subject,
            _MESSAGING_BODY_SIZE: len(payload),
            _MESSAGING_OPERATION: "send",
            "instance": self.instance,
        }
        if spec.metric_subject:
            attributes[_MESSAGING_TEMPLATE] = spec.metric_subject
        span = self.tracer.start_span(
            f"natskit.publish {name}", kind=SpanKind.PRODUCER, attributes=attributes
        )
        headers = spec.wire_headers()
        propagate.inject(headers, context=trace.set_span_in_context(span))
        return span, headers

    def record_publish(
        self, spec: PublishSpec, duration: float, err: BaseException | None, span: Span
    ) -> None:
        """Закрывает producer-спан и пишет publish-метрики.

        Label `subject` появляется только при `metric_subject`: реальный subject
        публикации (`alerts.employee.<id>`) неограничен по кардинальности.
        """
        if err is not None:
            span.record_exception(err)
            span.set_status(Status(StatusCode.ERROR, str(err)))
        span.end()
        ctx = trace.set_span_in_context(span)
        subject: Attrs = {"subject": spec.metric_subject} if spec.metric_subject else {}
        status = "error" if err is not None else "ok"
        self.published_total.add(1, self.attrs(status=status, **subject), context=ctx)
        self.publish_duration.record(duration, self.attrs(**subject), context=ctx)

    def start_consumer_span(
        self, subject: str, filter_: str, payload: bytes, headers: Mapping[str, str] | None
    ) -> Span:
        """Consumer-спан как child producer'а: trace-context берётся из заголовков сообщения.

        Имя спана — фильтр подписки: реальный subject wildcard-подписки размножил бы имена.
        """
        parent: Context | None = propagate.extract(dict(headers)) if headers else None
        return self.tracer.start_span(
            f"natskit.handle {filter_}",
            context=parent,
            kind=SpanKind.CONSUMER,
            attributes={
                _MESSAGING_SYSTEM: "nats",
                _MESSAGING_DESTINATION: subject,
                _MESSAGING_TEMPLATE: filter_,
                _MESSAGING_BODY_SIZE: len(payload),
                _MESSAGING_OPERATION: "process",
                "instance": self.instance,
            },
        )
