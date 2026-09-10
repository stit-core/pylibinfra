"""i18n-подсистема: виджет `I18NFormat`, выбор локали и движок переводов за портами.

Контент (`.ftl`) и фича смены языка — в сервисе; кит ничего не эмитит.
"""

from .format import I18N_FORMAT_KEY, I18NFormat
from .middleware import LOCALE_KEY, I18nMiddleware, install_i18n
from .resolver import (
    KEY_PREFIX,
    ChainResolver,
    FromCachePref,
    FromUserLanguage,
    LocalePrefStore,
    LocaleResolver,
)
from .translator import FluentTranslator, FormatFunc, Translator, build_translator

__all__ = [
    "I18N_FORMAT_KEY",
    "KEY_PREFIX",
    "LOCALE_KEY",
    "ChainResolver",
    "FluentTranslator",
    "FormatFunc",
    "FromCachePref",
    "FromUserLanguage",
    "I18NFormat",
    "I18nMiddleware",
    "LocalePrefStore",
    "LocaleResolver",
    "Translator",
    "build_translator",
    "install_i18n",
]
