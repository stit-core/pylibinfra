"""bottest — flow-тесты диалогов и unit-lane бота без Telegram и без Docker.

`BotClient`/`MockMessageManager` из aiogram_dialog, `JsonMemoryStorage`, `MemoryRedis`
и `bind_dialogs` (та же проводка, что у `BotRunner`). Требует extra `[tgbotkit]`.
"""

from aiogram_dialog.test_tools import BotClient, MockMessageManager

from .redis import MemoryLock, MemoryRedis
from .runner import bind_dialogs
from .storage import JsonMemoryStorage

__all__ = [
    "BotClient",
    "JsonMemoryStorage",
    "MemoryLock",
    "MemoryRedis",
    "MockMessageManager",
    "bind_dialogs",
]
