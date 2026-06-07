import pytest

from pylibinfra.cachekit import Config, new_config


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "REDIS_URL",
        "REDIS_POOL_SIZE",
        "REDIS_DIAL_TIMEOUT",
        "REDIS_IDEMPOTENCY_TTL",
    ):
        monkeypatch.delenv(var, raising=False)
    cfg = new_config()
    assert cfg.url == "redis://localhost:6379/0"
    assert cfg.pool_size == 10
    assert cfg.dial_timeout == 5.0
    assert cfg.idempotency_ttl == 86400.0


def test_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://cache:6379/1")
    monkeypatch.setenv("REDIS_POOL_SIZE", "25")
    monkeypatch.setenv("REDIS_IDEMPOTENCY_TTL", "60")
    cfg = Config()
    assert cfg.url == "redis://cache:6379/1"
    assert cfg.pool_size == 25
    assert cfg.idempotency_ttl == 60.0
