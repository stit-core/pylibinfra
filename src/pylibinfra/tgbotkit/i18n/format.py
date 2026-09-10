"""I18NFormat — Text-виджет aiogram_dialog, который рендерит ключ перевода."""

from __future__ import annotations

from typing import Any

from aiogram_dialog import DialogManager
from aiogram_dialog.widgets.common import WhenCondition
from aiogram_dialog.widgets.text import Text

I18N_FORMAT_KEY = "aiogd_i18n_format"
"""Ключ `middleware_data`, под которым `I18nMiddleware` кладёт функцию рендера."""


class I18NFormat(Text):
    """Виджет окна, хранящий ключ перевода; текст берётся из локали апдейта."""

    def __init__(self, key: str, when: WhenCondition = None) -> None:
        super().__init__(when=when)
        self.key = key

    async def _render_text(self, data: dict[str, Any], manager: DialogManager) -> str:
        fmt = manager.middleware_data.get(I18N_FORMAT_KEY)
        # Без настроенного i18n показываем сам ключ: непереведённое место видно сразу.
        if fmt is None:
            return self.key
        return str(fmt(self.key, data))
