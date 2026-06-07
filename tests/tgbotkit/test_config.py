import pytest
from pydantic import ValidationError

from pylibinfra.tgbotkit import Config, new_config


def test_defaults() -> None:
    cfg = Config(token="123:abc")
    assert cfg.mode == "polling"
    assert cfg.parse_mode == "HTML"
    assert cfg.dedup_enabled is True
    assert cfg.dedup_ttl == 600.0
    assert cfg.throttle_rps == 0
    assert cfg.flood_enabled is True
    assert cfg.rate_global == 30.0
    assert cfg.rate_per_chat == 1.0
    assert cfg.retry_after_attempts == 3
    assert cfg.webhook_path == "/tg/webhook"
    assert cfg.i18n.default_locale == "en"
    assert cfg.i18n.locales_dir == ""


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "999:xyz")
    monkeypatch.setenv("BOT_MODE", "webhook")
    monkeypatch.setenv("BOT_THROTTLE_RPS", "5")
    monkeypatch.setenv("BOT_I18N_DEFAULT_LOCALE", "ru")
    cfg = new_config()
    assert cfg.token.get_secret_value() == "999:xyz"
    assert cfg.mode == "webhook"
    assert cfg.throttle_rps == 5
    assert cfg.i18n.default_locale == "ru"


def test_mode_validation() -> None:
    with pytest.raises(ValidationError):
        Config(token="123:abc", mode="invalid")  # type: ignore[arg-type]
