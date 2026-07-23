import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from pydantic import ValidationError

from pylibinfra.telemetry import Config, span
from pylibinfra.telemetry.init import _otlp_headers, _otlp_url, _resource


def test_config_requires_service_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    with pytest.raises(ValidationError):
        Config()


def test_config_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SERVICE_NAME", "svc")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_INSECURE", "false")
    cfg = Config()
    assert cfg.service_name == "svc"
    assert cfg.otlp_insecure is False


def test_otlp_url_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SERVICE_NAME", "svc")
    insecure = Config(otlp_insecure=True, otlp_endpoint="host:4318")
    secure = Config(otlp_insecure=False, otlp_endpoint="host:4318")
    assert _otlp_url(insecure, "v1/traces") == "http://host:4318/v1/traces"
    assert _otlp_url(secure, "v1/logs") == "https://host:4318/v1/logs"


def test_otlp_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SERVICE_NAME", "svc")
    assert _otlp_headers(Config(otlp_bearer_token="")) is None
    assert _otlp_headers(Config(otlp_bearer_token="abc")) == {"Authorization": "Bearer abc"}


def test_resource_tenant_gating(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SERVICE_NAME", "svc")
    attrs = _resource(Config(service_name="svc")).attributes
    assert attrs["service.name"] == "svc"
    assert "tenant" not in attrs
    assert "service.namespace" not in attrs

    tenant_attrs = _resource(Config(service_name="svc", tenant="acme")).attributes
    assert tenant_attrs["tenant"] == "acme"
    assert tenant_attrs["service.namespace"] == "acme"


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
