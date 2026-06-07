"""I18NFormat — Text-виджет aiogram_dialog, рендерящий КЛЮЧ перевода (а не
готовый текст). Функцию форматирования кладёт I18nMiddleware в middleware_data
под I18N_FORMAT_KEY; если i18n не настроен — возвращаем ключ как есть (сразу
видно непереведённое место, без падения).
"""

from __future__ import annotations

from typing import Any

from aiogram_dialog import DialogManager
from aiogram_dialog.widgets.common import WhenCondition
from aiogram_dialog.widgets.text import Text

I18N_FORMAT_KEY = "aiogd_i18n_format"


class I18NFormat(Text):
    def __init__(self, key: str, when: WhenCondition = None) -> None:
        super().__init__(when=when)
        self.key = key

    async def _render_text(self, data: dict[str, Any], manager: DialogManager) -> str:
        fmt = manager.middleware_data.get(I18N_FORMAT_KEY)
        if fmt is None:
            return self.key
        result = fmt(self.key, data)
        return str(result)
