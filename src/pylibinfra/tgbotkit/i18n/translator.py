"""Translator — движок переводов за Protocol (подменяем без правки I18NFormat).
FluentTranslator грузит .ftl-каталоги (fluent.runtime): roots=<path>/{locale}.
Запрошенная локаль резолвится к каталогу из <path> (регистронезависимо; региональный
код вроде pt-br → базовый pt), неизвестная → default. Кэш FluentLocalization ограничен
числом каталогов локалей — произвольный language_code от юзера не растит его без предела.
Фолбэк-локаль в цепочке: недостающий в локали ключ берётся из default, а не падает.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from fluent.runtime import FluentLocalization, FluentResourceLoader

if TYPE_CHECKING:
    from pylibinfra.tgbotkit.config import I18nConfig

FormatFunc = Callable[[str, "dict[str, Any] | None"], str]


class Translator(Protocol):
    def for_locale(self, locale: str) -> FormatFunc: ...


class FluentTranslator:
    def __init__(self, default_locale: str, path: str, resources: list[str]) -> None:
        self._loader = FluentResourceLoader(os.path.join(path, "{locale}"))
        self._resources = resources
        self._default = default_locale
        self._known = _scan_locales(path)
        self._cache: dict[str, FluentLocalization] = {}

    def _resolve(self, locale: str) -> str:
        code = locale.lower()
        if code in self._known:
            return self._known[code]
        base = code.split("-", 1)[0]  # региональный код (pt-br) → базовый язык (pt)
        if base in self._known:
            return self._known[base]
        return self._default

    def _l10n(self, locale: str) -> FluentLocalization:
        # ключ кэша — резолвнутый каталог (∈ known ∪ {default}): кэш не растёт от мусорных кодов
        key = self._resolve(locale)
        cached = self._cache.get(key)
        if cached is None:
            # fallback на дефолт: недостающий в локали ключ возьмётся из default
            locales = [key] if key == self._default else [key, self._default]
            cached = FluentLocalization(locales, self._resources, self._loader)
            self._cache[key] = cached
        return cached

    def for_locale(self, locale: str) -> FormatFunc:
        return self._l10n(locale).format_value


def _scan_locales(path: str) -> dict[str, str]:
    """lowercase-код → имя каталога локали в <path> (для регистронезависимого матчинга)."""
    try:
        entries = os.listdir(path)
    except OSError:
        return {}
    return {name.lower(): name for name in entries if os.path.isdir(os.path.join(path, name))}


def build_translator(cfg: I18nConfig) -> Translator | None:
    """FluentTranslator из конфига; None без каталога локалей (i18n не настроен)."""
    if not cfg.locales_dir:
        return None
    return FluentTranslator(cfg.default_locale, cfg.locales_dir, cfg.resources)
