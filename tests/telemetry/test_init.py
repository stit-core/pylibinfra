from __future__ import annotations

import importlib
import io
import time

import pytest
from opentelemetry import metrics, trace
from opentelemetry._logs import get_logger_provider
from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor
from opentelemetry.propagate import get_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, InMemoryLogRecordExporter

from pylibinfra import logkit
from pylibinfra.telemetry import Config, ConfigError, Providers, init
from pylibinfra.telemetry.init import (
    DEFAULT_EXPORT_TIMEOUT,
    MIN_STEP_TIMEOUT,
    _resource,
    _url,
)


def test_disabled_sets_globals_without_exporters() -> None:
    providers = init(Config(disabled=True))

    assert trace.get_tracer_provider() is providers.tracer_provider
    assert metrics.get_meter_provider() is providers.meter_provider
    assert get_logger_provider() is providers.logger_provider
    # Ни одного экспортёра ни у одного сигнала: OTEL_SDK_DISABLED не ходит в сеть.
    assert providers.tracer_provider._active_span_processor._span_processors == ()
    assert providers.meter_provider._sdk_config.metric_readers == ()
    assert providers.logger_provider._multi_log_record_processor._log_record_processors == ()
    with trace.get_tracer("test").start_as_current_span("op") as span:
        assert span.is_recording() is False
    providers.shutdown(1.0)


def test_propagator_is_composite_even_when_disabled() -> None:
    init(Config(disabled=True))

    propagator = get_global_textmap()
    assert isinstance(propagator, CompositePropagator)
    assert set(propagator.fields) >= {"traceparent", "baggage"}


def test_disabled_skips_system_metrics() -> None:
    init(Config(disabled=True))

    assert SystemMetricsInstrumentor().is_instrumented_by_opentelemetry is False


def test_enabled_sets_globals_and_system_metrics() -> None:
    providers = init(Config(service_name="svc"))

    assert trace.get_tracer_provider() is providers.tracer_provider
    assert SystemMetricsInstrumentor().is_instrumented_by_opentelemetry is True
    providers.shutdown(1.0)


def test_resource_attributes() -> None:
    attrs = _resource(Config(service_name="svc", service_version="v1", environment="prod"))

    assert attrs.attributes["service.name"] == "svc"
    assert attrs.attributes["service.version"] == "v1"
    assert attrs.attributes["deployment.environment.name"] == "prod"
    assert "tenant" not in attrs.attributes
    assert "service.namespace" not in attrs.attributes


def test_tenant_attributes_are_gated() -> None:
    attrs = _resource(Config(service_name="svc", tenant="acme")).attributes

    assert attrs["tenant"] == "acme"
    assert attrs["service.namespace"] == "acme"


def test_signal_urls() -> None:
    cfg = Config(service_name="svc", otlp_endpoint="https://collector:4318/base")

    assert _url(cfg, "v1/metrics") == "https://collector:4318/base/v1/metrics"


def test_broken_endpoint_fails_init() -> None:
    broken = Config.model_construct(
        disabled=False, service_name="svc", otlp_endpoint="collector:4318", export_timeout=1.0
    )

    with pytest.raises(ConfigError, match="OTEL_EXPORTER_OTLP_ENDPOINT"):
        _url(broken, "v1/traces")


def test_init_rolls_back_already_built_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    # Имя `init` в пакете занято функцией, модуль берём из sys.modules.
    telemetry_init = importlib.import_module("pylibinfra.telemetry.init")

    # Каждый провайдер поднимает поток батчера: сбой на третьем сигнале обязан
    # погасить первые два, иначе потоки живут до конца процесса.
    class Spy:
        def __init__(self) -> None:
            self.stopped = False

        def shutdown(self) -> None:
            self.stopped = True

    tracer, meter = Spy(), Spy()

    def boom(*_: object) -> None:
        raise RuntimeError("telemetry: log exporter: boom")

    monkeypatch.setattr(telemetry_init, "_tracer_provider", lambda *_: tracer)
    monkeypatch.setattr(telemetry_init, "_meter_provider", lambda *_: meter)
    monkeypatch.setattr(telemetry_init, "_logger_provider", boom)

    with pytest.raises(RuntimeError, match="log exporter"):
        init(Config(service_name="svc"))

    assert tracer.stopped
    assert meter.stopped
    assert trace.get_tracer_provider() is not tracer


def test_error_logger_receives_sdk_diagnostics() -> None:
    import logging

    buf = io.StringIO()
    init(Config(disabled=True), error_logger=logkit.new(logkit.Config(), writer=buf))
    logging.getLogger("opentelemetry.exporter").error("collector unreachable")

    assert "collector unreachable" in buf.getvalue()


def test_shutdown_splits_the_budget_between_signals() -> None:
    providers = _fake_providers(export_timeout=10.0)
    seen: list[float] = []
    _record_steps(providers, seen)

    providers.shutdown(3.0)

    # Каждый шаг получает остаток дедлайна, делённый на число оставшихся шагов:
    # быстрый предыдущий шаг отдаёт своё время следующим, а не сгорает.
    assert [round(budget, 1) for budget in seen] == [1.0, 1.5, 3.0]


def test_shutdown_caps_the_budget_by_export_timeout() -> None:
    providers = _fake_providers(export_timeout=0.5)
    seen: list[float] = []
    _record_steps(providers, seen)

    providers.shutdown(30.0)

    assert seen == [0.5, 0.5, 0.5]


def test_shutdown_without_deadline_uses_export_timeout() -> None:
    providers = _fake_providers(export_timeout=2.0)
    seen: list[float] = []
    _record_steps(providers, seen)

    providers.shutdown()

    assert seen == [2.0, 2.0, 2.0]


def test_shutdown_keeps_a_floor_for_later_steps() -> None:
    # Шаг, переживший свой бюджет (недоступный коллектор), не обнуляет остальные:
    # нулевой дедлайн гарантированно роняет их и выбрасывает очередь.
    providers = _fake_providers(export_timeout=10.0)
    seen: list[float] = []
    providers._stop_tracer = lambda _: time.sleep(0.2)  # type: ignore[method-assign]
    providers._stop_meter = seen.append  # type: ignore[method-assign]
    providers._stop_logger = seen.append  # type: ignore[method-assign]

    providers.shutdown(0.1)

    assert seen == [MIN_STEP_TIMEOUT, MIN_STEP_TIMEOUT]


def test_shutdown_falls_back_to_the_default_export_timeout() -> None:
    providers = _fake_providers(export_timeout=0.0)
    seen: list[float] = []
    _record_steps(providers, seen)

    providers.shutdown()

    assert seen == [DEFAULT_EXPORT_TIMEOUT] * 3


def test_shutdown_collects_errors_and_keeps_going() -> None:
    providers = _fake_providers(export_timeout=1.0)
    seen: list[str] = []
    providers._stop_tracer = lambda _: seen.append("tracer")  # type: ignore[method-assign]
    providers._stop_meter = _boom  # type: ignore[method-assign]
    providers._stop_logger = lambda _: seen.append("logger")  # type: ignore[method-assign]

    with pytest.raises(ExceptionGroup) as excinfo:
        providers.shutdown(1.0)

    assert seen == ["tracer", "logger"]
    assert "meter provider" in str(excinfo.value.exceptions[0])


def test_shutdown_flushes_queued_logs() -> None:
    exporter = InMemoryLogRecordExporter()
    logger_provider = LoggerProvider()
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(exporter, schedule_delay_millis=60_000)
    )
    providers = init(Config(disabled=True))
    providers.logger_provider = logger_provider
    logkit.new(logkit.Config(silent=True), otel=logger_provider).info("queued")

    providers.shutdown(2.0)

    assert [record.log_record.body for record in exporter.get_finished_logs()] == ["queued"]


def _boom(_: float) -> None:
    raise RuntimeError("stop failed")


def _fake_providers(*, export_timeout: float) -> Providers:
    providers = init(Config(disabled=True))
    providers.export_timeout = export_timeout
    return providers


def _record_steps(providers: Providers, seen: list[float]) -> None:
    providers._stop_tracer = seen.append  # type: ignore[method-assign]
    providers._stop_meter = seen.append  # type: ignore[method-assign]
    providers._stop_logger = seen.append  # type: ignore[method-assign]
