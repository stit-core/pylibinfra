"""Config кита: дефолты, чтение ENV, fail-fast на невалидном значении, пароль в ошибках."""

from __future__ import annotations

import pytest

from pylibinfra.cachekit import Config, ConfigError, new_config
from pylibinfra.cachekit.config import redact


def test_defaults() -> None:
    cfg = new_config()

    assert cfg.url.get_secret_value() == "redis://localhost:6379/0"
    assert cfg.key_prefix == ""
    assert cfg.pool_size == 10
    assert cfg.max_retries == 3
    assert cfg.dial_timeout == 5.0
    assert cfg.read_timeout == 3.0
    assert cfg.write_timeout == 3.0
    assert cfg.ping_on_start is True
    assert cfg.skip_version_check is False


def test_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "rediss://user:pass@cache:6380/2")
    monkeypatch.setenv("REDIS_KEY_PREFIX", "svc:")
    monkeypatch.setenv("REDIS_POOL_SIZE", "25")
    monkeypatch.setenv("REDIS_MAX_RETRIES", "-1")
    monkeypatch.setenv("REDIS_DIAL_TIMEOUT", "1.5")
    monkeypatch.setenv("REDIS_PING_ON_START", "false")
    monkeypatch.setenv("REDIS_SKIP_VERSION_CHECK", "true")

    cfg = new_config()

    assert cfg.url.get_secret_value() == "rediss://user:pass@cache:6380/2"
    assert cfg.key_prefix == "svc:"
    assert cfg.pool_size == 25
    assert cfg.max_retries == -1
    assert cfg.dial_timeout == 1.5
    assert cfg.ping_on_start is False
    assert cfg.skip_version_check is True


def test_zero_timeouts_are_valid() -> None:
    cfg = Config.model_validate(
        {"REDIS_DIAL_TIMEOUT": 0, "REDIS_READ_TIMEOUT": 0, "REDIS_WRITE_TIMEOUT": 0}
    )
    assert cfg.read_timeout == 0.0


def test_unix_socket_url_is_valid() -> None:
    cfg = Config.model_validate({"REDIS_URL": "unix:///var/run/redis.sock"})
    assert cfg.url.get_secret_value().startswith("unix://")


@pytest.mark.parametrize(
    ("env", "value"),
    [
        ("REDIS_POOL_SIZE", "0"),
        ("REDIS_POOL_SIZE", "-1"),
        ("REDIS_MAX_RETRIES", "-2"),
        ("REDIS_DIAL_TIMEOUT", "-1"),
        ("REDIS_READ_TIMEOUT", "-1"),
        ("REDIS_WRITE_TIMEOUT", "-1"),
        ("REDIS_URL", "http://localhost:6379"),
        ("REDIS_URL", "redis://localhost:notaport"),
        ("REDIS_READ_TIMEOUT", "soon"),
    ],
)
def test_invalid_value_fails_fast(monkeypatch: pytest.MonkeyPatch, env: str, value: str) -> None:
    monkeypatch.setenv(env, value)

    with pytest.raises(ConfigError) as excinfo:
        new_config()

    assert str(excinfo.value).startswith("cachekit: invalid config: ")


def test_empty_url_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # env_ignore_empty: заданная пустой переменная читается как незаданная.
    monkeypatch.setenv("REDIS_URL", "")
    assert new_config().url.get_secret_value() == "redis://localhost:6379/0"

    with pytest.raises(ValueError, match="REDIS_URL"):
        Config.model_validate({"REDIS_URL": ""})


def test_config_error_names_the_env_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_POOL_SIZE", "0")

    with pytest.raises(ConfigError, match="REDIS_POOL_SIZE"):
        new_config()


def test_password_stays_out_of_errors_and_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://user:sup3rsecret@cache:notaport/0")

    with pytest.raises(ConfigError) as excinfo:
        new_config()

    assert "sup3rsecret" not in str(excinfo.value)
    monkeypatch.setenv("REDIS_URL", "redis://user:sup3rsecret@cache:6379/0")
    assert "sup3rsecret" not in repr(new_config())


def test_redact_hides_password_from_driver_text() -> None:
    url = "redis://user:sup3rsecret@cache:6379/0"

    assert redact(f"bad url {url}", url) == "bad url redis://user:***@cache:6379/0"
    assert redact("no userinfo", "redis://cache:6379/0") == "no userinfo"
