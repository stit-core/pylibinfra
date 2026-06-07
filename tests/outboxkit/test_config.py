import pytest

from pylibinfra.outboxkit import Config, new_config


def test_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OUTBOX_BATCH_SIZE", raising=False)
    monkeypatch.delenv("OUTBOX_INTERVAL", raising=False)
    cfg = new_config()
    assert cfg.batch_size == 100
    assert cfg.interval == 1.0


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTBOX_BATCH_SIZE", "250")
    monkeypatch.setenv("OUTBOX_INTERVAL", "0.5")
    cfg = Config()
    assert cfg.batch_size == 250
    assert cfg.interval == 0.5
