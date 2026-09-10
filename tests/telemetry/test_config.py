from __future__ import annotations

import pytest
from pydantic import ValidationError

from pylibinfra.telemetry import Config, ConfigError, new_config, signal_url
from pylibinfra.telemetry.config import canonical_header, parse_headers


def test_service_name_is_required() -> None:
    with pytest.raises(ConfigError, match="OTEL_SERVICE_NAME is required"):
        new_config()


def test_disabled_config_needs_no_service_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")

    cfg = new_config()

    assert cfg.disabled is True
    assert cfg.service_name == ""


def test_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SERVICE_NAME", "svc")
    monkeypatch.setenv("OTEL_SERVICE_VERSION", "v1.2.3")
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://otlp.example.com/otlp")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", "0.25")
    monkeypatch.setenv("OTEL_EXPORT_TIMEOUT", "3")
    monkeypatch.setenv("OTEL_TRACES_BATCH_DELAY", "2")
    monkeypatch.setenv("OTEL_METRICS_INTERVAL", "15")
    monkeypatch.setenv("OTEL_LOGS_BATCH_DELAY", "0.5")
    monkeypatch.setenv("OTEL_TENANT", "acme")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "x-scope-orgid=acme")
    monkeypatch.setenv("OTEL_TOKEN", "tok")

    cfg = new_config()

    assert cfg.service_name == "svc"
    assert cfg.service_version == "v1.2.3"
    assert cfg.environment == "prod"
    assert cfg.otlp_endpoint == "https://otlp.example.com/otlp"
    assert cfg.sample_ratio == 0.25
    assert cfg.export_timeout == 3
    assert cfg.trace_batch_delay == 2
    assert cfg.metric_interval == 15
    assert cfg.log_batch_delay == 0.5
    assert cfg.tenant == "acme"
    assert cfg.otlp_headers == {"X-Scope-Orgid": "acme"}
    assert cfg.otlp_bearer_token.get_secret_value() == "tok"


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SERVICE_NAME", "svc")

    cfg = new_config()

    assert cfg.otlp_endpoint == "http://localhost:4318"
    assert (cfg.sample_ratio, cfg.export_timeout) == (1.0, 10.0)
    assert (cfg.trace_batch_delay, cfg.metric_interval, cfg.log_batch_delay) == (5.0, 10.0, 1.0)
    assert (cfg.service_version, cfg.environment) == ("dev", "dev")
    assert cfg.otlp_headers == {}


def test_endpoint_must_be_a_full_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SERVICE_NAME", "svc")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "localhost:4318")

    with pytest.raises(ConfigError, match="OTEL_EXPORTER_OTLP_ENDPOINT"):
        new_config()


@pytest.mark.parametrize(
    ("endpoint", "url"),
    [
        ("http://localhost:4318", "http://localhost:4318/v1/traces"),
        ("https://otlp.example.com/otlp", "https://otlp.example.com/otlp/v1/traces"),
        ("https://otlp.example.com/otlp/", "https://otlp.example.com/otlp/v1/traces"),
    ],
)
def test_signal_url_keeps_scheme_and_path(endpoint: str, url: str) -> None:
    assert signal_url(endpoint, "v1/traces") == url


@pytest.mark.parametrize("value", ["-0.1", "1.5"])
def test_sample_ratio_range(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("OTEL_SERVICE_NAME", "svc")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", value)

    with pytest.raises(ConfigError, match="OTEL_TRACES_SAMPLER_ARG"):
        new_config()


@pytest.mark.parametrize(
    "env",
    [
        "OTEL_EXPORT_TIMEOUT",
        "OTEL_TRACES_BATCH_DELAY",
        "OTEL_METRICS_INTERVAL",
        "OTEL_LOGS_BATCH_DELAY",
    ],
)
def test_timers_must_be_positive(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    monkeypatch.setenv("OTEL_SERVICE_NAME", "svc")
    monkeypatch.setenv(env, "0")

    with pytest.raises(ConfigError, match="greater than 0"):
        new_config()


def test_headers_are_percent_decoded_and_canonical() -> None:
    assert parse_headers("k1=v1, authorization=Bearer%20a%2Bb") == {
        "K1": "v1",
        "Authorization": "Bearer a+b",
    }
    assert parse_headers("") == {}


@pytest.mark.parametrize("value", ["novalue", "=v"])
def test_malformed_header_pair_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="want key=value pairs"):
        parse_headers(value)


@pytest.mark.parametrize("key", ["bad key", "bad:key", ""])
def test_invalid_header_name_is_rejected(key: str) -> None:
    with pytest.raises(ValueError, match="not a valid header name"):
        canonical_header(key)


def test_token_overrides_authorization_header() -> None:
    cfg = Config(
        service_name="svc",
        otlp_headers="authorization=Bearer from-env,x-tenant=acme",
        otlp_bearer_token="tok",
    )

    assert cfg.headers() == {"Authorization": "Bearer tok", "X-Tenant": "acme"}


def test_headers_of_a_wrong_type_are_rejected_by_the_model() -> None:
    with pytest.raises(ValidationError, match="valid dictionary"):
        Config(service_name="svc", otlp_headers=["k=v"])  # type: ignore[arg-type]


def test_headers_without_token() -> None:
    cfg = Config(service_name="svc", otlp_headers={"x-tenant": "acme"})

    assert cfg.headers() == {"X-Tenant": "acme"}


def test_token_is_not_shown_in_repr() -> None:
    cfg = Config(service_name="svc", otlp_bearer_token="super-secret")

    assert "super-secret" not in repr(cfg)


def test_headers_are_not_shown_in_repr() -> None:
    # Authorization корп ingest'а приходит именно этой переменной.
    cfg = Config(service_name="svc", otlp_headers="authorization=Bearer%20header-secret")

    assert "header-secret" not in repr(cfg)
    assert cfg.headers()["Authorization"] == "Bearer header-secret"


def test_malformed_header_error_does_not_echo_the_entry() -> None:
    with pytest.raises(ValueError, match="entry #2") as exc_info:
        parse_headers("k=v,supersecrettoken")

    assert "supersecrettoken" not in str(exc_info.value)


def test_endpoint_error_redacts_userinfo() -> None:
    with pytest.raises(ValueError, match="want a full URL") as exc_info:
        signal_url("ftp://user:hunter2@collector:4318", "v1/traces")

    message = str(exc_info.value)
    assert "hunter2" not in message
    assert "<redacted>@collector:4318" in message


def test_config_is_frozen() -> None:
    cfg = Config(service_name="svc")

    with pytest.raises(ValidationError):
        cfg.service_name = "other"  # type: ignore[misc]
