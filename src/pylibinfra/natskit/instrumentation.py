"""Span- и метрик-helper'ы для bus/jetstream.

W3C-trace-context (traceparent/tracestate) инжектится в nats-headers на
producer-стороне и извлекается на consumer-стороне через словарь-carrier
(opentelemetry.propagate.inject/extract), чтобы consumer-span был child'ом
producer-span'а. Эмитит nats_messages_{published,consumed}_total,
nats_{publish,handle}_duration_seconds, nats_inflight_messages.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import metrics, propagate, trace
from opentelemetry.trace import Span, SpanKind, Tracer

_MESSAGING_SYSTEM = "messaging.system"
_MESSAGING_DESTINATION = "messaging.destination.name"
_MESSAGING_BODY_SIZE = "messaging.message.body.size"


class NatsInstruments:
    def __init__(self) -> None:
        meter = metrics.get_meter("app")
        self.tracer: Tracer = trace.get_tracer("app")

        self.published_total = meter.create_counter(
            "nats_messages_published_total", description="NATS messages published"
        )
        self.publish_duration = meter.create_histogram(
            "nats_publish_duration_seconds",
            unit="s",
            description="Time to publish a NATS message",
        )
        self.consumed_total = meter.create_counter(
            "nats_messages_consumed_total", description="NATS messages consumed"
        )
        self.handle_duration = meter.create_histogram(
            "nats_handle_duration_seconds",
            unit="s",
            description="Subscriber handler duration",
        )
        self.inflight = meter.create_up_down_counter(
            "nats_inflight_messages",
            description="Messages currently being handled (per-subject)",
        )

    @contextmanager
    def publish_span(self, subject: str, data: bytes) -> Iterator[tuple[Span, dict[str, str]]]:
        """Producer-span + carrier-headers с инжектнутым trace-context."""
        headers: dict[str, str] = {}
        with self.tracer.start_as_current_span(
            f"natskit.publish {subject}",
            kind=SpanKind.PRODUCER,
            attributes={
                _MESSAGING_SYSTEM: "nats",
                _MESSAGING_DESTINATION: subject,
                _MESSAGING_BODY_SIZE: len(data),
            },
        ) as span:
            propagate.inject(headers)
            yield span, headers

    def start_publish_span(self, subject: str, data: bytes) -> tuple[Span, dict[str, str]]:
        """Detached producer-span (не current) + carrier-headers — для JetStream:
        одиночная и батч-публикация идут конкурентно, поэтому span нельзя делать
        current (как start_as_current_span). Вызывающий обязан вызвать span.end()."""
        span = self.tracer.start_span(
            f"natskit.publish {subject}",
            kind=SpanKind.PRODUCER,
            attributes={
                _MESSAGING_SYSTEM: "nats",
                _MESSAGING_DESTINATION: subject,
                _MESSAGING_BODY_SIZE: len(data),
            },
        )
        headers: dict[str, str] = {}
        propagate.inject(headers, context=trace.set_span_in_context(span))
        return span, headers

    def consumer_span(self, subject: str, data: bytes, headers: dict[str, str] | None) -> Span:
        """Consumer-span как child producer'а (trace-context из headers)."""
        ctx = propagate.extract(headers) if headers else None
        return self.tracer.start_span(
            f"natskit.handle {subject}",
            context=ctx,
            kind=SpanKind.CONSUMER,
            attributes={
                _MESSAGING_SYSTEM: "nats",
                _MESSAGING_DESTINATION: subject,
                _MESSAGING_BODY_SIZE: len(data),
            },
        )
