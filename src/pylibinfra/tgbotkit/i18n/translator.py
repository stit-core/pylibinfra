"""Translator — движок переводов за Protocol (подменяем без правки I18NFormat).
FluentTranslator грузит .ftl-каталоги (fluent.runtime): roots=<path>/{locale},
per-locale FluentLocalization кэшируется; фолбэк-локаль добавляется в цепочку,
чтобы недостающий ключ брался из дефолта, а не падал.
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
        self._cache: dict[str, FluentLocalization] = {}

    def _l10n(self, locale: str) -> FluentLocalization:
        cached = self._cache.get(locale)
        if cached is None:
            # fallback на дефолт: недостающий в locale ключ возьмётся из default
            locales = [locale] if locale == self._default else [locale, self._default]
            cached = FluentLocalization(locales, self._resources, self._loader)
            self._cache[locale] = cached
        return cached

    def for_locale(self, locale: str) -> FormatFunc:
        return self._l10n(locale).format_value


def build_translator(cfg: I18nConfig) -> Translator | None:
    """FluentTranslator из конфига; None без каталога локалей (i18n не настроен)."""
    if not cfg.locales_dir:
        return None
    return FluentTranslator(cfg.default_locale, cfg.locales_dir, cfg.resources)
