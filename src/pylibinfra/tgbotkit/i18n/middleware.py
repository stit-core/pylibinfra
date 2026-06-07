"""I18nMiddleware (inner на dp.update): резолвит локаль и кладёт format-функцию в
middleware_data[I18N_FORMAT_KEY] + data["locale"]. На dp.update (а не на
message/callback) — чтобы покрыть и фоновые dialog-рендеры (они идут через тот же
propagate_event("update")). install_i18n — no-op без настроенного каталога.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Dispatcher
from aiogram.types import TelegramObject

from pylibinfra.tgbotkit.config import I18nConfig

from .format import I18N_FORMAT_KEY
from .resolver import FromUserLanguage, LocaleResolver
from .translator import FluentTranslator, Translator

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class I18nMiddleware(BaseMiddleware):
    def __init__(
        self, translator: Translator, resolver: LocaleResolver, default_locale: str
    ) -> None:
        self._tr = translator
        self._resolver = resolver
        self._default = default_locale

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        locale = await self._resolver.resolve(event, data) or self._default
        data["locale"] = locale
        data[I18N_FORMAT_KEY] = self._tr.for_locale(locale)
        return await handler(event, data)


def install_i18n(
    dp: Dispatcher,
    cfg: I18nConfig,
    translator: Translator | None = None,
    resolver: LocaleResolver | None = None,
) -> None:
    if translator is None:
        if not cfg.locales_dir:
            return  # i18n не настроен
        translator = FluentTranslator(cfg.default_locale, cfg.locales_dir, cfg.resources)
    dp.update.middleware(
        I18nMiddleware(translator, resolver or FromUserLanguage(), cfg.default_locale)
    )
