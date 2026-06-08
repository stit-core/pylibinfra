"""Delivery.localize — locale-канал фоновой доставки (вне I18nMiddleware)."""

from pathlib import Path

from pylibinfra.tgbotkit import FluentTranslator, Translator
from pylibinfra.tgbotkit.notifications.delivery import Delivery


def _delivery(translator: Translator | None = None, default_locale: str = "en") -> Delivery:
    # bg_factory/bot не нужны localize — он трогает только translator
    return Delivery(None, None, None, translator, default_locale)  # type: ignore[arg-type]


def test_localize_returns_key_without_translator() -> None:
    text = _delivery().localize("ru", "notifications-order-paid", {"order_id": "o1"})
    assert text == "notifications-order-paid"


def test_localize_renders_via_translator(tmp_path: Path) -> None:
    (tmp_path / "ru").mkdir()
    (tmp_path / "ru" / "main.ftl").write_text("hi = Привет { $name }\n", encoding="utf-8")
    tr = FluentTranslator("ru", str(tmp_path), ["main.ftl"])
    assert "Боб" in _delivery(tr, "ru").localize("ru", "hi", {"name": "Боб"})


def test_localize_falls_back_to_default_locale(tmp_path: Path) -> None:
    (tmp_path / "en").mkdir()
    (tmp_path / "en" / "main.ftl").write_text("hi = Hello\n", encoding="utf-8")
    tr = FluentTranslator("en", str(tmp_path), ["main.ftl"])
    # locale=None → default_locale
    assert "Hello" in _delivery(tr, "en").localize(None, "hi", None)
