from __future__ import annotations

import pytest
from pydantic import ValidationError

from pylibinfra.httpclient import (
    BackendConfig,
    BackendsConfig,
    BreakerConfig,
    Config,
    ConfigError,
    new_backends_config,
    new_breaker_config,
    new_config,
    new_config_prefixed,
)


def test_defaults_match_golibinfra() -> None:
    c = Config()

    assert c.timeout == 10.0
    assert c.max_retries == 3
    assert c.retry_wait == 0.2
    assert c.retry_max_wait == 5.0
    assert c.retry_non_idempotent is False
    assert c.max_redirects == 10
    assert c.allow_cross_host_redirects is False
    assert c.sse_max_event_bytes == 1 << 20
    assert c.base_url == ""
    assert c.breaker.failure_threshold == 5
    assert c.breaker.recovery_timeout == 30.0


def test_zero_config_is_safe_and_gets_defaults() -> None:
    # Config с нулями безопасен: клиент без таймаута собрать нельзя.
    c = Config(
        timeout=0,
        retry_wait=0,
        retry_max_wait=0,
        max_redirects=0,
        sse_max_event_bytes=0,
        breaker=BreakerConfig(recovery_timeout=0),
    )

    assert c.timeout == 10.0
    assert c.retry_wait == 0.2
    assert c.retry_max_wait == 5.0
    assert c.max_redirects == 10
    assert c.sse_max_event_bytes == 1 << 20
    assert c.breaker.recovery_timeout == 30.0


def test_zero_retries_and_threshold_are_deliberate_choices() -> None:
    c = Config(max_retries=0, breaker=BreakerConfig(failure_threshold=0))

    assert c.max_retries == 0
    assert c.breaker.failure_threshold == 0


@pytest.mark.parametrize(
    "values",
    [
        {"timeout": -1.0},
        {"max_retries": -1},
        {"retry_wait": -1.0},
        {"retry_max_wait": -1.0},
        {"max_redirects": -1},
        {"sse_max_event_bytes": -1},
        {"retry_wait": 10.0, "retry_max_wait": 0.001},
    ],
)
def test_invalid_values_rejected(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(values)


@pytest.mark.parametrize("values", [{"failure_threshold": -1}, {"recovery_timeout": -1.0}])
def test_invalid_breaker_values_rejected(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        BreakerConfig.model_validate(values)


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPCLIENT_TIMEOUT", "2.5")
    monkeypatch.setenv("HTTPCLIENT_MAX_RETRIES", "7")
    monkeypatch.setenv("HTTPCLIENT_RETRY_WAIT", "0.05")
    monkeypatch.setenv("HTTPCLIENT_RETRY_MAX_WAIT", "1.5")
    monkeypatch.setenv("HTTPCLIENT_RETRY_NON_IDEMPOTENT", "true")
    monkeypatch.setenv("HTTPCLIENT_MAX_REDIRECTS", "3")
    monkeypatch.setenv("HTTPCLIENT_ALLOW_CROSS_HOST_REDIRECTS", "true")
    monkeypatch.setenv("HTTPCLIENT_SSE_MAX_EVENT_BYTES", "2048")
    monkeypatch.setenv("HTTPCLIENT_BASE_URL", "http://api.local")
    monkeypatch.setenv("HTTPCLIENT_BREAKER_FAILURE_THRESHOLD", "9")
    monkeypatch.setenv("HTTPCLIENT_BREAKER_RECOVERY_TIMEOUT", "4")

    c = new_config()

    assert c.timeout == 2.5
    assert c.max_retries == 7
    assert c.retry_wait == 0.05
    assert c.retry_max_wait == 1.5
    assert c.retry_non_idempotent is True
    assert c.max_redirects == 3
    assert c.allow_cross_host_redirects is True
    assert c.sse_max_event_bytes == 2048
    assert c.base_url == "http://api.local"
    assert c.breaker.failure_threshold == 9
    assert c.breaker.recovery_timeout == 4.0


def test_invalid_env_raises_kit_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPCLIENT_MAX_RETRIES", "-1")

    with pytest.raises(ConfigError, match="httpclient: invalid config"):
        new_config()


def test_unparsable_env_raises_kit_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPCLIENT_TIMEOUT", "soon")

    with pytest.raises(ConfigError):
        new_config()


def test_prefixed_config_falls_back_to_bare_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPCLIENT_TIMEOUT", "9")
    monkeypatch.setenv("HTTPCLIENT_MAX_RETRIES", "5")
    monkeypatch.setenv("HTTPCLIENT_BREAKER_FAILURE_THRESHOLD", "8")
    monkeypatch.setenv("AMOCRM_HTTPCLIENT_TIMEOUT", "1")
    monkeypatch.setenv("AMOCRM_HTTPCLIENT_BREAKER_RECOVERY_TIMEOUT", "2")

    c = new_config_prefixed("AMOCRM")

    assert c.timeout == 1.0
    assert c.max_retries == 5
    assert c.breaker.failure_threshold == 8
    assert c.breaker.recovery_timeout == 2.0


def test_prefixed_config_ignores_empty_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPCLIENT_TIMEOUT", "9")
    monkeypatch.setenv("AMOCRM_HTTPCLIENT_TIMEOUT", "")

    assert new_config_prefixed("AMOCRM").timeout == 9.0


def test_breaker_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPCLIENT_BREAKER_FAILURE_THRESHOLD", "10")
    monkeypatch.setenv("HTTPCLIENT_BREAKER_RECOVERY_TIMEOUT", "5.0")

    b = new_breaker_config()

    assert b.failure_threshold == 10
    assert b.recovery_timeout == 5.0


def test_backend_config_requires_base_url() -> None:
    with pytest.raises(ValidationError):
        BackendConfig()  # type: ignore[call-arg]


def test_backend_config_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        BackendConfig.model_validate({"base_url": "http://crm", "base_ur": "x"})


def test_global_env_does_not_bleed_into_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    # Глобальный HTTPCLIENT_BASE_URL не должен молча становиться base_url
    # именованного бэкенда — иначе трафик уходит на чужой хост.
    monkeypatch.setenv("HTTPCLIENT_BASE_URL", "http://GLOBAL")
    monkeypatch.setenv("HTTPCLIENT_TIMEOUT", "99")
    monkeypatch.setenv("HTTPCLIENT_BACKENDS", '{"crm": {"base_url": "http://crm", "timeout": 3.0}}')

    cfg = new_backends_config()
    runtime = cfg.backends["crm"].client_config()

    assert runtime.base_url == "http://crm"
    assert runtime.timeout == 3.0
    assert runtime.max_retries == 3


def test_backend_without_base_url_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPCLIENT_BACKENDS", '{"crm": {"timeout": 3.0}}')

    with pytest.raises(ConfigError):
        new_backends_config()


def test_backends_config_defaults_to_empty() -> None:
    assert BackendsConfig().backends == {}
