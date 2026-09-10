"""Translator — движок переводов за портом; production-реализация поверх fluent."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from fluent.runtime import FluentLocalization, FluentResourceLoader

if TYPE_CHECKING:  # pragma: no cover
    from pylibinfra.tgbotkit.config import I18nConfig

FormatFunc = Callable[[str, "dict[str, Any] | None"], str]
"""Рендер ключа перевода с аргументами; неизвестный ключ fluent отдаёт как есть."""


class Translator(Protocol):
    """Порт движка переводов: по локали отдаёт функцию рендера ключа."""

    def for_locale(self, locale: str) -> FormatFunc:
        """Рендер для локали; неизвестная локаль обслуживается дефолтной."""


class FluentTranslator:
    """`.ftl`-каталоги fluent из `<path>/<locale>` с фолбэком на дефолтную локаль.

    Запрошенная локаль резолвится к существующему каталогу регистронезависимо,
    региональный код (`pt-BR`) — к базовому языку (`pt`), неизвестная — к дефолтной.
    Кэш ограничен числом каталогов, поэтому произвольный `language_code` от клиента
    не растит его без предела.
    """

    def __init__(self, default_locale: str, path: str, resources: list[str]) -> None:
        self._loader = FluentResourceLoader(os.path.join(path, "{locale}"))
        self._resources = resources
        self._default = default_locale
        self._known = _scan_locales(path)
        self._cache: dict[str, FluentLocalization] = {}

    def for_locale(self, locale: str) -> FormatFunc:
        """Рендер ключей в этой локали; недостающий в ней ключ берётся из дефолтной."""
        return self._l10n(locale).format_value

    def _resolve(self, locale: str) -> str:
        code = locale.lower()
        if code in self._known:
            return self._known[code]
        base = code.split("-", 1)[0]
        if base in self._known:
            return self._known[base]
        return self._default

    def _l10n(self, locale: str) -> FluentLocalization:
        key = self._resolve(locale)
        cached = self._cache.get(key)
        if cached is None:
            locales = [key] if key == self._default else [key, self._default]
            cached = FluentLocalization(locales, self._resources, self._loader)
            self._cache[key] = cached
        return cached


def build_translator(cfg: I18nConfig) -> Translator | None:
    """`FluentTranslator` из конфига; `None` без каталога локалей — i18n не настроен."""
    if not cfg.locales_dir:
        return None
    return FluentTranslator(cfg.default_locale, cfg.locales_dir, cfg.resources)


def _scan_locales(path: str) -> dict[str, str]:
    """lowercase-код → имя каталога локали (для регистронезависимого матчинга)."""
    try:
        entries = os.listdir(path)
    except OSError:
        return {}
    return {name.lower(): name for name in entries if os.path.isdir(os.path.join(path, name))}
