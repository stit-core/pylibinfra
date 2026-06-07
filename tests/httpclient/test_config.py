import pytest

from pylibinfra.httpclient import BreakerConfig, Config, new_breaker_config, new_config


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


def test_breaker_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPCLIENT_BREAKER_FAILURE_THRESHOLD", "10")
    monkeypatch.setenv("HTTPCLIENT_BREAKER_RECOVERY_TIMEOUT", "5.0")
    monkeypatch.setenv("HTTPCLIENT_BREAKER_SUCCESS_THRESHOLD", "1")

    b = new_breaker_config()
    assert b.failure_threshold == 10
    assert b.recovery_timeout == 5.0
    assert b.success_threshold == 1
