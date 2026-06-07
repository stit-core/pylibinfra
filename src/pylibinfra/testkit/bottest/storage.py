"""JsonMemoryStorage — MemoryStorage, который round-trip'ит dialog_data через JSON
на set_data: ловит несериализуемое (datetime/Decimal/ORM/множества) в unit/flow-
тестах ДО того, как оно упадёт в проде на реальном RedisStorage.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage


class JsonMemoryStorage(MemoryStorage):
    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        try:
            checked = json.loads(json.dumps(data))
        except (TypeError, ValueError) as exc:
            raise TypeError(f"dialog data is not JSON-serializable: {exc}") from exc
        await super().set_data(key, checked)
