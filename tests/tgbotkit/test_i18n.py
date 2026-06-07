from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pylibinfra.tgbotkit import FluentTranslator, I18NFormat
from pylibinfra.tgbotkit.i18n.format import I18N_FORMAT_KEY


async def test_i18nformat_falls_back_to_key_when_not_configured() -> None:
    widget = I18NFormat("greeting")
    manager = SimpleNamespace(middleware_data={})
    text = await widget._render_text({}, manager)  # type: ignore[arg-type]
    assert text == "greeting"


async def test_i18nformat_uses_format_func() -> None:
    widget = I18NFormat("greeting")

    def fmt(key: str, data: dict[str, Any] | None) -> str:
        name = (data or {}).get("name")
        return f"Hello {name}"

    manager = SimpleNamespace(middleware_data={I18N_FORMAT_KEY: fmt})
    text = await widget._render_text({"name": "Bob"}, manager)  # type: ignore[arg-type]
    assert text == "Hello Bob"


def test_fluent_translator_renders_ftl(tmp_path: Path) -> None:
    (tmp_path / "en").mkdir()
    (tmp_path / "en" / "main.ftl").write_text("greeting = Hi { $name }\n", encoding="utf-8")
    tr = FluentTranslator("en", str(tmp_path), ["main.ftl"])
    fmt = tr.for_locale("en")
    assert "Bob" in fmt("greeting", {"name": "Bob"})


def test_fluent_translator_falls_back_to_default_locale(tmp_path: Path) -> None:
    (tmp_path / "en").mkdir()
    (tmp_path / "en" / "main.ftl").write_text("only-en = English only\n", encoding="utf-8")
    (tmp_path / "ru").mkdir()
    (tmp_path / "ru" / "main.ftl").write_text("ru-key = по-русски\n", encoding="utf-8")
    tr = FluentTranslator("en", str(tmp_path), ["main.ftl"])
    fmt = tr.for_locale("ru")
    # ключа нет в ru → берётся из дефолта en
    assert "English only" in fmt("only-en", None)
