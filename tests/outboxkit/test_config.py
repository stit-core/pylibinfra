import pytest
from pydantic import ValidationError

from pylibinfra.outboxkit import Config, PublishMode, new_config


def test_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("OUTBOX_BATCH_SIZE", "OUTBOX_INTERVAL", "OUTBOX_PUBLISH_MODE", "OUTBOX_MAX_ATTEMPTS"):
        monkeypatch.delenv(k, raising=False)
    cfg = new_config()
    assert cfg.batch_size == 100
    assert cfg.interval == 1.0
    assert cfg.publish_mode == PublishMode.BATCH
    assert cfg.max_attempts == 0


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTBOX_BATCH_SIZE", "250")
    monkeypatch.setenv("OUTBOX_INTERVAL", "0.5")
    monkeypatch.setenv("OUTBOX_PUBLISH_MODE", "ordered")
    monkeypatch.setenv("OUTBOX_MAX_ATTEMPTS", "10")
    cfg = Config()
    assert cfg.batch_size == 250
    assert cfg.interval == 0.5
    assert cfg.publish_mode == PublishMode.ORDERED
    assert cfg.max_attempts == 10


def test_invalid_publish_mode_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTBOX_PUBLISH_MODE", "bogus")
    with pytest.raises(ValidationError):
        new_config()
