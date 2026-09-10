from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from opentelemetry import trace
from opentelemetry._logs import _internal as logs_internal
from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor
from opentelemetry.metrics import _internal as metrics_internal
from opentelemetry.propagate import get_global_textmap, set_global_textmap
from opentelemetry.util._once import Once

from pylibinfra.telemetry.errorlog import OTEL_LOGGER


def _reset_globals() -> None:
    # OTel держит провайдеры set-once на процесс: без сброса guard'а init()
    # второго теста молча не применился бы.
    trace._TRACER_PROVIDER_SET_ONCE = Once()
    trace._TRACER_PROVIDER = None
    metrics_internal._METER_PROVIDER_SET_ONCE = Once()
    metrics_internal._METER_PROVIDER = None
    logs_internal._LOGGER_PROVIDER_SET_ONCE = Once()
    logs_internal._LOGGER_PROVIDER = None


@pytest.fixture(autouse=True)
def _isolate_globals(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for env in ("OTEL_SERVICE_NAME", "OTEL_SDK_DISABLED", "OTEL_EXPORTER_OTLP_ENDPOINT"):
        monkeypatch.delenv(env, raising=False)
    propagator = get_global_textmap()
    otel_log = logging.getLogger(OTEL_LOGGER)
    handlers, propagate, level = list(otel_log.handlers), otel_log.propagate, otel_log.level
    _reset_globals()

    yield

    _reset_globals()
    set_global_textmap(propagator)
    for handler in list(otel_log.handlers):
        otel_log.removeHandler(handler)
    for handler in handlers:
        otel_log.addHandler(handler)
    otel_log.propagate, otel_log.level = propagate, level
    instrumentor = SystemMetricsInstrumentor()
    if instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.uninstrument()
