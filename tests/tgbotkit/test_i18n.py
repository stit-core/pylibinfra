"""i18n: движок fluent, виджет, выбор локали и проводка middleware."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiogram import Dispatcher

from pylibinfra.cachekit import MemoryCache
from pylibinfra.tgbotkit import (
    ChainResolver,
    FluentTranslator,
    FromCachePref,
    FromUserLanguage,
    I18nConfig,
    I18NFormat,
    LocalePrefStore,
    build_translator,
    install_i18n,
)
from pylibinfra.tgbotkit.i18n import I18N_FORMAT_KEY, LOCALE_KEY, I18nMiddleware


def _catalog(root: Path, locale: str, body: str) -> Path:
    (root / locale).mkdir(parents=True, exist_ok=True)
    (root / locale / "main.ftl").write_text(body, encoding="utf-8")
    return root


async def test_widget_falls_back_to_key_when_i18n_is_off() -> None:
    widget = I18NFormat("greeting")
    manager = SimpleNamespace(middleware_data={})
    assert await widget._render_text({}, manager) == "greeting"  # type: ignore[arg-type]


async def test_widget_renders_through_format_func() -> None:
    def fmt(key: str, data: dict[str, Any] | None) -> str:
        return f"Hello {(data or {}).get('name')}"

    widget = I18NFormat("greeting")
    manager = SimpleNamespace(middleware_data={I18N_FORMAT_KEY: fmt})
    assert await widget._render_text({"name": "Bob"}, manager) == "Hello Bob"  # type: ignore[arg-type]


def test_translator_renders_arguments(tmp_path: Path) -> None:
    _catalog(tmp_path, "en", "greeting = Hi { $name }\n")
    tr = FluentTranslator("en", str(tmp_path), ["main.ftl"])
    assert "Bob" in tr.for_locale("en")("greeting", {"name": "Bob"})


def test_translator_falls_back_to_default_locale(tmp_path: Path) -> None:
    _catalog(tmp_path, "en", "only-en = English only\n")
    _catalog(tmp_path, "ru", "ru-key = по-русски\n")
    tr = FluentTranslator("en", str(tmp_path), ["main.ftl"])
    assert "English only" in tr.for_locale("ru")("only-en", None)


def test_translator_normalizes_regional_code(tmp_path: Path) -> None:
    _catalog(tmp_path, "pt", "hi = Olá\n")
    tr = FluentTranslator("en", str(tmp_path), ["main.ftl"])
    assert "Olá" in tr.for_locale("pt-BR")("hi", None)


def test_translator_cache_is_bounded_by_known_locales(tmp_path: Path) -> None:
    _catalog(tmp_path, "en", "hi = Hello\n")
    tr = FluentTranslator("en", str(tmp_path), ["main.ftl"])
    for code in ("xx", "yy-1", "zz", "garbage", "ru-RU", "fr"):
        assert "Hello" in tr.for_locale(code)("hi", None)
    assert set(tr._cache) == {"en"}


def test_translator_survives_missing_directory() -> None:
    tr = FluentTranslator("en", "/nonexistent/locales", ["main.ftl"])
    assert tr._known == {}


def test_build_translator_is_none_without_catalog() -> None:
    assert build_translator(I18nConfig()) is None


def test_build_translator_reads_config(tmp_path: Path) -> None:
    _catalog(tmp_path, "en", "greeting = Hi { $name }\n")
    tr = build_translator(I18nConfig(default_locale="en", locales_dir=str(tmp_path)))
    assert tr is not None
    assert "Bob" in tr.for_locale("en")("greeting", {"name": "Bob"})


async def test_from_user_language_reads_client_locale() -> None:
    resolver = FromUserLanguage()
    data = {"event_from_user": SimpleNamespace(language_code="ru")}
    assert await resolver.resolve(SimpleNamespace(), data) == "ru"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "data", [{}, {"event_from_user": SimpleNamespace(language_code="")}, {"event_from_user": None}]
)
async def test_from_user_language_is_none_without_code(data: dict[str, Any]) -> None:
    assert await FromUserLanguage().resolve(SimpleNamespace(), data) is None  # type: ignore[arg-type]


async def test_locale_pref_store_roundtrip() -> None:
    store = LocalePrefStore(MemoryCache())
    assert await store.get(7) is None
    await store.set(7, "ru")
    assert await store.get(7) == "ru"


async def test_locale_pref_store_honours_ttl() -> None:
    cache = MemoryCache()
    await LocalePrefStore(cache, ttl=60.0).set(7, "ru")
    assert await cache.get("locale:7") == b"ru"


async def test_from_cache_pref_resolves_saved_choice() -> None:
    store = LocalePrefStore(MemoryCache())
    await store.set(7, "de")
    resolver = FromCachePref(store)
    data = {"event_from_user": SimpleNamespace(id=7)}
    assert await resolver.resolve(SimpleNamespace(), data) == "de"  # type: ignore[arg-type]
    assert await resolver.resolve(SimpleNamespace(), {}) is None  # type: ignore[arg-type]


async def test_chain_resolver_takes_first_answer() -> None:
    store = LocalePrefStore(MemoryCache())
    await store.set(7, "de")
    chain = ChainResolver(FromCachePref(store), FromUserLanguage())
    data = {"event_from_user": SimpleNamespace(id=7, language_code="ru")}
    assert await chain.resolve(SimpleNamespace(), data) == "de"  # type: ignore[arg-type]


async def test_chain_resolver_is_none_when_nobody_knows() -> None:
    chain = ChainResolver(FromUserLanguage())
    assert await chain.resolve(SimpleNamespace(), {}) is None  # type: ignore[arg-type]


async def test_middleware_puts_locale_and_format_into_data(tmp_path: Path) -> None:
    _catalog(tmp_path, "ru", "hi = Привет\n")
    tr = FluentTranslator("ru", str(tmp_path), ["main.ftl"])
    mw = I18nMiddleware(tr, FromUserLanguage(), "ru")
    seen: dict[str, Any] = {}

    async def handler(event: Any, data: dict[str, Any]) -> None:
        seen.update(data)

    await mw(handler, SimpleNamespace(), {"event_from_user": SimpleNamespace(language_code="ru")})
    assert seen[LOCALE_KEY] == "ru"
    assert "Привет" in seen[I18N_FORMAT_KEY]("hi", None)


async def test_middleware_falls_back_to_default_locale(tmp_path: Path) -> None:
    _catalog(tmp_path, "en", "hi = Hello\n")
    tr = FluentTranslator("en", str(tmp_path), ["main.ftl"])
    mw = I18nMiddleware(tr, FromUserLanguage(), "en")
    seen: dict[str, Any] = {}

    async def handler(event: Any, data: dict[str, Any]) -> None:
        seen.update(data)

    await mw(handler, SimpleNamespace(), {})
    assert seen[LOCALE_KEY] == "en"


def test_install_i18n_is_noop_without_catalog() -> None:
    dp = Dispatcher()
    install_i18n(dp, I18nConfig())
    assert not [
        mw for mw in dp.update.middleware._middlewares if type(mw).__name__ == "I18nMiddleware"
    ]


def test_install_i18n_wires_middleware(tmp_path: Path) -> None:
    _catalog(tmp_path, "en", "hi = Hello\n")
    dp = Dispatcher()
    install_i18n(dp, I18nConfig(default_locale="en", locales_dir=str(tmp_path)))
    assert [
        type(mw).__name__
        for mw in dp.update.middleware._middlewares
        if type(mw).__name__ == "I18nMiddleware"
    ] == ["I18nMiddleware"]
