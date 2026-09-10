import pytest
from pydantic import ValidationError

from pylibinfra.httpkit import Config, ConfigError, new_config
from pylibinfra.httpkit.cors import CORSConfig
from pylibinfra.httpkit.idempotency import IdempotencyConfig
from pylibinfra.httpkit.maxbody import MaxBodyConfig
from pylibinfra.httpkit.ratelimit import RateLimitConfig


def test_defaults() -> None:
    cfg = Config()
    assert cfg.addr == ":8080"
    assert cfg.read_timeout == 15.0
    assert cfg.idle_timeout == 60.0
    assert cfg.shutdown_timeout == 15.0
    assert cfg.api_title == "API"
    assert cfg.api_version == "0.1.0"
    assert cfg.docs_enabled is True
    assert cfg.ready_probe_timeout == 5.0
    assert cfg.quiet_paths == ["/health", "/ready"]
    assert cfg.max_body.max_bytes == 1048576
    assert cfg.rate_limit.rps == 100
    assert cfg.rate_limit.burst == 200
    assert cfg.rate_limit.trust_proxy is False
    assert cfg.security.hsts == "max-age=31536000; includeSubDomains"
    assert cfg.security.content_type_options == "nosniff"
    assert cfg.security.frame_options == "DENY"
    assert cfg.security.referrer_policy == "strict-origin-when-cross-origin"
    assert cfg.security.csp == ""
    assert cfg.cors.origins == []
    assert cfg.cors.methods == ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    assert cfg.cors.headers == ["Authorization", "Content-Type", "X-Request-ID"]
    assert cfg.cors.expose_headers == [
        "X-Request-ID",
        "Location",
        "Retry-After",
        "Idempotent-Replayed",
    ]
    assert cfg.cors.max_age == 600
    assert cfg.cors.allow_credentials is False
    assert cfg.idempotency.header == "Idempotency-Key"
    assert cfg.idempotency.ttl == 86400.0
    assert cfg.idempotency.reserve_ttl == 60.0
    assert cfg.idempotency.max_body_bytes == 65536
    assert cfg.idempotency.store_timeout == 2.0
    assert cfg.idempotency.fail_closed is False


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTP_ADDR", ":9090")
    monkeypatch.setenv("HTTP_READ_TIMEOUT", "5")
    monkeypatch.setenv("HTTP_API_TITLE", "MyAPI")
    monkeypatch.setenv("HTTP_DOCS_ENABLED", "false")
    monkeypatch.setenv("HTTP_READY_PROBE_TIMEOUT", "1.5")
    monkeypatch.setenv("HTTP_QUIET_PATHS", "/health,/ready,/metrics")
    monkeypatch.setenv("HTTP_MAX_BODY_BYTES", "2048")
    monkeypatch.setenv("HTTP_RATE_LIMIT_RPS", "50")
    monkeypatch.setenv("HTTP_RATE_LIMIT_TRUST_PROXY", "true")
    monkeypatch.setenv("HTTP_CORS_ORIGINS", "https://a.example,https://b.example")
    monkeypatch.setenv("HTTP_CORS_EXPOSE_HEADERS", "X-Request-ID")
    monkeypatch.setenv("HTTP_CSP", "default-src 'self'")
    monkeypatch.setenv("HTTP_FRAME_OPTIONS", "SAMEORIGIN")
    monkeypatch.setenv("HTTP_IDEMPOTENCY_STORE_TIMEOUT", "0.5")
    monkeypatch.setenv("HTTP_IDEMPOTENCY_FAIL_CLOSED", "true")

    cfg = new_config()

    assert cfg.addr == ":9090"
    assert cfg.read_timeout == 5.0
    assert cfg.api_title == "MyAPI"
    assert cfg.docs_enabled is False
    assert cfg.ready_probe_timeout == 1.5
    assert cfg.quiet_paths == ["/health", "/ready", "/metrics"]
    assert cfg.max_body.max_bytes == 2048
    assert cfg.rate_limit.rps == 50
    assert cfg.rate_limit.trust_proxy is True
    assert cfg.cors.origins == ["https://a.example", "https://b.example"]
    assert cfg.cors.expose_headers == ["X-Request-ID"]
    assert cfg.security.csp == "default-src 'self'"
    assert cfg.security.frame_options == "SAMEORIGIN"
    assert cfg.idempotency.store_timeout == 0.5
    assert cfg.idempotency.fail_closed is True


@pytest.mark.parametrize(
    ("env", "value"),
    [
        ("HTTP_ADDR", ""),
        ("HTTP_SHUTDOWN_TIMEOUT", "0"),
        ("HTTP_READY_PROBE_TIMEOUT", "0"),
        ("HTTP_READ_TIMEOUT", "-1"),
        ("HTTP_API_TITLE", "not-a-number-but-fine"),
    ],
)
def test_new_config_reports_kit_error(
    monkeypatch: pytest.MonkeyPatch, env: str, value: str
) -> None:
    monkeypatch.setenv(env, value)
    if env in ("HTTP_ADDR", "HTTP_API_TITLE"):
        # env_ignore_empty: пустое значение читается как незаданное, дефолт валиден
        monkeypatch.setenv("HTTP_SHUTDOWN_TIMEOUT", "0")
    with pytest.raises(ConfigError) as exc:
        new_config()
    assert str(exc.value).startswith("httpkit: invalid config:")


def test_new_config_rejects_unparsable_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTP_READ_TIMEOUT", "not-a-number")
    with pytest.raises(ConfigError):
        new_config()


def test_direct_construction_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        Config(shutdown_timeout=0)


def test_cors_credentials_incompatible_with_wildcard() -> None:
    with pytest.raises(ValidationError):
        CORSConfig(origins=["*"], allow_credentials=True)


def test_cors_max_age_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        CORSConfig(max_age=-1)


def test_rate_limit_and_max_body_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        RateLimitConfig(rps=-1)
    with pytest.raises(ValidationError):
        MaxBodyConfig(max_bytes=-1)


@pytest.mark.parametrize(
    "values",
    [
        {"header": ""},
        {"ttl": 0},
        {"reserve_ttl": 0},
        {"store_timeout": 0},
        {"max_body_bytes": 0},
    ],
)
def test_idempotency_config_rules(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        IdempotencyConfig(**values)  # type: ignore[arg-type]


def test_zero_value_config_is_usable() -> None:
    assert Config().addr == ":8080"


def test_read_timeout_and_api_version_rules() -> None:
    with pytest.raises(ValidationError, match="read_timeout"):
        Config(read_timeout=-1)
    with pytest.raises(ValidationError, match="api_title"):
        Config(api_version="")


def test_empty_addr_is_rejected() -> None:
    with pytest.raises(ValidationError, match="addr"):
        Config(addr="")
