"""I18nMiddleware: локаль апдейта и функция рендера в `middleware_data`."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Dispatcher
from aiogram.types import TelegramObject

from pylibinfra.tgbotkit.config import I18nConfig

from .format import I18N_FORMAT_KEY
from .resolver import FromUserLanguage, LocaleResolver
from .translator import Translator, build_translator

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]

LOCALE_KEY = "locale"
"""Ключ `middleware_data` с локалью апдейта — её читают обработчики консьюмера."""


class I18nMiddleware(BaseMiddleware):
    """Кладёт локаль апдейта и её `FormatFunc` в данные обработчика."""

    def __init__(
        self, translator: Translator, resolver: LocaleResolver, default_locale: str
    ) -> None:
        self._translator = translator
        self._resolver = resolver
        self._default = default_locale

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        """Резолвит локаль апдейта и кладёт её вместе с `FormatFunc` в данные."""
        locale = await self._resolver.resolve(event, data) or self._default
        data[LOCALE_KEY] = locale
        data[I18N_FORMAT_KEY] = self._translator.for_locale(locale)
        return await handler(event, data)


def install_i18n(
    dp: Dispatcher,
    cfg: I18nConfig,
    translator: Translator | None = None,
    resolver: LocaleResolver | None = None,
) -> None:
    """Ставит `I18nMiddleware` на `dp.update`; без каталога локалей — no-op.

    На `dp.update`, а не на `message`/`callback_query`: через него же идут фоновые
    рендеры диалога, которым текст нужен так же.
    """
    engine = translator if translator is not None else build_translator(cfg)
    if engine is None:
        return
    dp.update.middleware(I18nMiddleware(engine, resolver or FromUserLanguage(), cfg.default_locale))
