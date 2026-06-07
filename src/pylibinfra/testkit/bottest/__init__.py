"""bottest — flow-тест диалогов без Telegram (требует extra [tgbotkit]):
JsonMemoryStorage (ловит несериализуемый dialog_data), BotClient/MockMessageManager
(из aiogram_dialog.test_tools), bind_dialogs (deferred-проводка как в рантайме kit'а).
"""

from aiogram_dialog.test_tools import BotClient, MockMessageManager

from .runner import bind_dialogs
from .storage import JsonMemoryStorage

__all__ = ["BotClient", "JsonMemoryStorage", "MockMessageManager", "bind_dialogs"]
