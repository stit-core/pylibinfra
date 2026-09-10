from __future__ import annotations

import io
import json
from collections.abc import Iterator
from typing import Any

import pytest
from opentelemetry._logs import NoOpLoggerProvider, SeverityNumber
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.trace import TracerProvider

from pylibinfra._internal import otelkit
from pylibinfra.logkit import Config, RedactConfig, ServiceConfig, new, register_attr


@pytest.fixture
def exporter() -> InMemoryLogRecordExporter:
    return InMemoryLogRecordExporter()


@pytest.fixture
def provider(exporter: InMemoryLogRecordExporter) -> Iterator[LoggerProvider]:
    lp = LoggerProvider()
    lp.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    yield lp
    lp.shutdown()


def emitted(exporter: InMemoryLogRecordExporter) -> list[Any]:
    return list(exporter.get_finished_logs())


def test_record_carries_body_severity_and_attributes(
    provider: LoggerProvider, exporter: InMemoryLogRecordExporter
) -> None:
    new(Config(), otel=provider).with_(component="worker").info("hello", k="v", n=1)

    record = emitted(exporter)[0]
    assert record.log_record.body == "hello"
    assert record.log_record.severity_number is SeverityNumber.INFO
    assert record.log_record.severity_text == "INFO"
    attrs = dict(record.log_record.attributes)
    assert attrs["component"] == "worker"
    assert attrs["k"] == "v"
    assert attrs["n"] == 1


def test_scope_is_the_kit_scope(
    provider: LoggerProvider, exporter: InMemoryLogRecordExporter
) -> None:
    new(Config(), otel=provider).info("x")

    scope = emitted(exporter)[0].instrumentation_scope
    assert scope.name == otelkit.scope("logkit")
    assert scope.version == otelkit.version()


@pytest.mark.parametrize(
    ("method", "severity"),
    [
        ("debug", SeverityNumber.DEBUG),
        ("info", SeverityNumber.INFO),
        ("warn", SeverityNumber.WARN),
        ("error", SeverityNumber.ERROR),
    ],
)
def test_severity_numbers(
    provider: LoggerProvider,
    exporter: InMemoryLogRecordExporter,
    method: str,
    severity: SeverityNumber,
) -> None:
    log = new(Config(level="debug"), otel=provider)
    getattr(log, method)("x")

    assert emitted(exporter)[0].log_record.severity_number is severity


def test_level_filter_applies_to_both_sinks(
    buf: io.StringIO, provider: LoggerProvider, exporter: InMemoryLogRecordExporter
) -> None:
    new(Config(level="warn"), writer=buf, otel=provider).info("filtered")

    assert emitted(exporter) == []
    assert buf.getvalue() == ""


def test_trace_context_is_attached_by_the_sdk(
    provider: LoggerProvider, exporter: InMemoryLogRecordExporter
) -> None:
    log = new(Config(), otel=provider)
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("op") as span:
        log.info("traced")

    record = emitted(exporter)[0].log_record
    assert record.trace_id == span.get_span_context().trace_id
    assert record.span_id == span.get_span_context().span_id


def test_ctx_attrs_reach_both_sinks(
    buf: io.StringIO, provider: LoggerProvider, exporter: InMemoryLogRecordExporter
) -> None:
    register_attr("tenant", lambda: "acme")
    new(Config(), writer=buf, otel=provider).info("x")

    assert dict(emitted(exporter)[0].log_record.attributes)["tenant"] == "acme"
    assert json.loads(buf.getvalue())["tenant"] == "acme"


def test_redact_applies_before_fan_out(
    buf: io.StringIO, provider: LoggerProvider, exporter: InMemoryLogRecordExporter
) -> None:
    log = new(Config(), writer=buf, otel=provider, redact=RedactConfig(enabled=True))
    log.info("login john@example.com", email="john@example.com")

    record = emitted(exporter)[0].log_record
    assert record.body == "login [REDACTED]"
    assert dict(record.attributes)["email"] == "[REDACTED]"
    assert json.loads(buf.getvalue())["email"] == "[REDACTED]"


def test_service_fields_reach_the_otel_sink(
    provider: LoggerProvider, exporter: InMemoryLogRecordExporter
) -> None:
    new(Config(), otel=provider, service=ServiceConfig(service="api")).info("x")

    assert dict(emitted(exporter)[0].log_record.attributes)["service"] == "api"


def test_reserved_record_keys_survive_as_attributes(
    provider: LoggerProvider, exporter: InMemoryLogRecordExporter
) -> None:
    # В OTLP имена записи и атрибутов не конкурируют, поэтому поле не теряется.
    new(Config(), otel=provider).info("real message", msg="user field", level="oops")

    record = emitted(exporter)[0].log_record
    assert record.body == "real message"
    assert record.severity_text == "INFO"
    attrs = dict(record.attributes)
    assert attrs["msg"] == "user field"
    assert attrs["level"] == "oops"


def test_noop_provider_adds_no_sink(buf: io.StringIO) -> None:
    log = new(Config(), writer=buf, otel=NoOpLoggerProvider())
    log.info("x")

    assert json.loads(buf.getvalue())["msg"] == "x"


def test_silent_config_keeps_the_otel_sink(
    provider: LoggerProvider, exporter: InMemoryLogRecordExporter
) -> None:
    new(Config(silent=True), otel=provider).info("only otlp")

    assert emitted(exporter)[0].log_record.body == "only otlp"
