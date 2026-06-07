"""i18n-подсистема: I18NFormat-виджет + middleware-проводка; движок (fluent) за
Protocol Translator, выбор локали — за Protocol LocaleResolver. Контент (.ftl) и
фича смены языка — в шаблоне.
"""

from .format import I18N_FORMAT_KEY, I18NFormat
from .middleware import I18nMiddleware, install_i18n
from .resolver import (
    ChainResolver,
    FromCachePref,
    FromUserLanguage,
    LocalePrefStore,
    LocaleResolver,
)
from .translator import FluentTranslator, FormatFunc, Translator

__all__ = [
    "I18N_FORMAT_KEY",
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
    "install_i18n",
]
