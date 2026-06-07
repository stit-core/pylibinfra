import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from pydantic import ValidationError

from pylibinfra.telemetry import Config, init, span


def test_config_requires_service_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    with pytest.raises(ValidationError):
        Config()


def test_config_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SERVICE_NAME", "svc")
    monkeypatch.setenv("OTEL_STDOUT_ONLY", "true")
    cfg = Config()
    assert cfg.service_name == "svc"
    assert cfg.stdout_only is True


def test_init_stdout_and_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SERVICE_NAME", "svc")
    monkeypatch.setenv("OTEL_STDOUT_ONLY", "true")
    providers = init(Config())
    assert providers.tracer_provider is not None
    providers.shutdown()


def test_span_reraises_and_records_error() -> None:
    # span() читает глобальный tracer provider; OTel выставляет его set-once.
    # Если SDK-провайдер уже есть — цепляем свой exporter к нему, иначе ставим свой.
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        trace.set_tracer_provider(TracerProvider())
        provider = trace.get_tracer_provider()
        assert isinstance(provider, TracerProvider)

    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    with pytest.raises(ValueError, match="boom"), span("op", attr="v"):
        raise ValueError("boom")

    finished = exporter.get_finished_spans()
    assert any(s.status.status_code == StatusCode.ERROR for s in finished)
