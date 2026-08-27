import pytest
from pydantic import ValidationError

from pylibinfra.httpclient import (
    BackendConfig,
    BackendsConfig,
    BreakerConfig,
    Config,
    new_breaker_config,
    new_config,
)


def test_config_defaults() -> None:
    c = Config()
    assert c.timeout == 10.0
    assert c.max_retries == 3
    assert c.retry_wait == 0.2
    assert c.base_url == ""
    assert c.breaker.failure_threshold == 5


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPCLIENT_TIMEOUT", "2.5")
    monkeypatch.setenv("HTTPCLIENT_MAX_RETRIES", "7")
    monkeypatch.setenv("HTTPCLIENT_RETRY_WAIT", "0.05")
    monkeypatch.setenv("HTTPCLIENT_BASE_URL", "http://api.local")

    c = new_config()
    assert c.timeout == 2.5
    assert c.max_retries == 7
    assert c.retry_wait == 0.05
    assert c.base_url == "http://api.local"


def test_breaker_config_defaults() -> None:
    b = BreakerConfig()
    assert b.failure_threshold == 5
    assert b.recovery_timeout == 30.0
    assert b.success_threshold == 3
    assert b.name == "httpclient"


def test_breaker_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPCLIENT_BREAKER_FAILURE_THRESHOLD", "10")
    monkeypatch.setenv("HTTPCLIENT_BREAKER_RECOVERY_TIMEOUT", "5.0")
    monkeypatch.setenv("HTTPCLIENT_BREAKER_SUCCESS_THRESHOLD", "1")

    b = new_breaker_config()
    assert b.failure_threshold == 10
    assert b.recovery_timeout == 5.0
    assert b.success_threshold == 1


def test_breaker_name_is_not_env_driven(monkeypatch: pytest.MonkeyPatch) -> None:
    # Глобальная env-ручка имени перекрывала бы имя каждому именованному бэкенду
    # и схлопывала per-backend ряды метрик — имя задаётся только кодом.
    monkeypatch.setenv("HTTPCLIENT_BREAKER_NAME", "crm")

    assert new_breaker_config().name == "httpclient"


def test_backend_config_requires_base_url() -> None:
    with pytest.raises(ValidationError):
        BackendConfig()  # type: ignore[call-arg]


def test_backend_config_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        BackendConfig.model_validate({"base_url": "http://crm", "base_ur": "x"})


def test_global_env_does_not_bleed_into_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: глобальный HTTPCLIENT_BASE_URL не должен молча становиться
    # base_url именованного бэкенда — иначе трафик уходит на чужой хост.
    monkeypatch.setenv("HTTPCLIENT_BASE_URL", "http://GLOBAL")
    monkeypatch.setenv("HTTPCLIENT_TIMEOUT", "99")
    monkeypatch.setenv("HTTPCLIENT_BACKENDS", '{"crm": {"base_url": "http://crm", "timeout": 3.0}}')

    cfg = BackendsConfig()

    assert cfg.backends["crm"].base_url == "http://crm"
    assert cfg.backends["crm"].timeout == 3.0
    assert cfg.backends["crm"].max_retries == 3


def test_backend_without_base_url_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPCLIENT_BASE_URL", "http://GLOBAL")
    monkeypatch.setenv("HTTPCLIENT_BACKENDS", '{"crm": {"timeout": 3.0}}')

    with pytest.raises(ValidationError):
        BackendsConfig()
