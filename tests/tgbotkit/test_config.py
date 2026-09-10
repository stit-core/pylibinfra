"""Config: дефолты, строгий ENV-источник по алиасам и fail-fast валидация."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from pylibinfra.tgbotkit import Config, ConfigError, I18nConfig, new_config

from .conftest import TOKEN, cfg


def test_defaults() -> None:
    c = cfg()
    assert c.mode == "polling"
    assert c.parse_mode == "HTML"
    assert (c.api_timeout, c.shutdown_timeout) == (60.0, 10.0)
    assert (c.dedup_enabled, c.dedup_ttl) == (True, 600.0)
    assert (c.throttle_rps, c.state_ttl) == (0, 0)
    assert (c.rate_global, c.rate_per_chat, c.retry_after_attempts) == (30.0, 1.0, 3)
    assert c.webhook_path == "/tg/webhook"
    assert (c.i18n.default_locale, c.i18n.locales_dir, c.i18n.resources) == ("en", "", ["main.ftl"])


def test_token_is_required() -> None:
    with pytest.raises(ConfigError, match="BOT_TOKEN"):
        new_config()


def test_env_is_read_by_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", TOKEN)
    monkeypatch.setenv("BOT_THROTTLE_RPS", "5")
    monkeypatch.setenv("BOT_I18N_DEFAULT_LOCALE", "ru")
    monkeypatch.setenv("BOT_I18N_RESOURCES", "main.ftl, errors.ftl")
    c = new_config()
    assert c.token.get_secret_value() == TOKEN
    assert c.throttle_rps == 5
    assert c.i18n.default_locale == "ru"
    assert c.i18n.resources == ["main.ftl", "errors.ftl"]


def test_bare_field_name_is_not_an_env_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", TOKEN)
    monkeypatch.setenv("mode", "webhook")
    assert new_config().mode == "polling"


def test_empty_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", TOKEN)
    monkeypatch.setenv("BOT_PARSE_MODE", "")
    assert new_config().parse_mode == "HTML"


def test_config_is_frozen() -> None:
    with pytest.raises(ValidationError):
        cfg().mode = "webhook"  # type: ignore[misc]


@pytest.mark.parametrize("token", ["", "no-colon", "abc:secret", "123456:"])
def test_malformed_token_rejected(token: str) -> None:
    with pytest.raises(ValidationError, match="BOT_TOKEN"):
        Config(token=SecretStr(token))


def test_config_error_hides_token_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "super-secret-but-invalid")
    with pytest.raises(ConfigError) as exc:
        new_config()
    assert "super-secret" not in str(exc.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("api_timeout", 0.0),
        ("dedup_ttl", 0.0),
        ("rate_max_wait", -1.0),
        ("shutdown_timeout", -1.0),
        ("throttle_rps", -1),
        ("state_ttl", -1),
        ("rate_global", -1.0),
        ("rate_per_chat", -1.0),
        ("retry_after_attempts", 0),
        ("webhook_path", "tg/webhook"),
    ],
)
def test_invalid_values_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        cfg(**{field: value})


@pytest.mark.parametrize("mode", ["polling", "webhook"])
def test_bad_webhook_secret_charset_rejected(mode: str) -> None:
    with pytest.raises(ValidationError, match="BOT_WEBHOOK_SECRET"):
        cfg(
            mode=mode, webhook_base_url="https://bot.example", webhook_secret=SecretStr("has space")
        )


def test_webhook_mode_requires_https_base_url() -> None:
    with pytest.raises(ValidationError, match="BOT_WEBHOOK_BASE_URL"):
        cfg(mode="webhook", webhook_base_url="http://bot.example", webhook_secret=SecretStr("s3"))


def test_webhook_mode_requires_secret() -> None:
    with pytest.raises(ValidationError, match="BOT_WEBHOOK_SECRET is required"):
        cfg(mode="webhook", webhook_base_url="https://bot.example")


def test_webhook_url_joins_base_and_path() -> None:
    c = cfg(
        mode="webhook",
        webhook_base_url="https://bot.example/",
        webhook_secret=SecretStr("s3cret"),
        webhook_path="/hook",
    )
    assert c.webhook_url() == "https://bot.example/hook"


def test_webhook_url_empty_without_base_url() -> None:
    assert cfg().webhook_url() == ""


def test_i18n_defaults_do_not_touch_filesystem() -> None:
    assert I18nConfig().locales_dir == ""


def test_i18n_rejects_missing_directory() -> None:
    with pytest.raises(ValidationError, match="not a directory"):
        I18nConfig(locales_dir="/nonexistent/locales")


def test_i18n_rejects_missing_default_catalog(tmp_path: Path) -> None:
    (tmp_path / "ru").mkdir()
    with pytest.raises(ValidationError, match="no catalog"):
        I18nConfig(default_locale="en", locales_dir=str(tmp_path))


def test_i18n_accepts_configured_catalog(tmp_path: Path) -> None:
    (tmp_path / "en").mkdir()
    c = I18nConfig(default_locale="en", locales_dir=str(tmp_path))
    assert c.locales_dir == str(tmp_path)


@pytest.mark.parametrize(("field", "value"), [("default_locale", ""), ("resources", [])])
def test_i18n_rejects_empty_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        I18nConfig(**{field: value})
