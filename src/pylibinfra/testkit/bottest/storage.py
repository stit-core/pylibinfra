"""JsonMemoryStorage — MemoryStorage, ловящий несериализуемые данные диалога."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage


class JsonMemoryStorage(MemoryStorage):
    """Гоняет данные диалога через JSON на записи — как это делает боевой `RedisStorage`.

    `datetime`, `Decimal`, ORM-объект или множество в `dialog_data` падают здесь
    `TypeError`, а не в проде на первой записи в Redis.
    """

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        """Пишет данные; несериализуемое значение — `TypeError` с исходной причиной."""
        try:
            checked = json.loads(json.dumps(data))
        except (TypeError, ValueError) as exc:
            raise TypeError(f"bottest: dialog data is not JSON-serializable: {exc}") from exc
        await super().set_data(key, checked)
