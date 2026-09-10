"""bind_dialogs — отложенная проводка диалогов для тестов, без Redis и без сервера."""

from __future__ import annotations

from aiogram import Dispatcher
from aiogram_dialog import BgManagerFactory, setup_dialogs
from aiogram_dialog.api.protocols.message_manager import MessageManagerProtocol
from dishka import AsyncContainer
from dishka.integrations.aiogram import setup_dishka


def bind_dialogs(
    dp: Dispatcher,
    container: AsyncContainer | None = None,
    message_manager: MessageManagerProtocol | None = None,
) -> BgManagerFactory:
    """Повторяет проводку `BotRunner`, но с in-memory event-isolation.

    Зовётся после всех `include_router` — как и в рантайме, `setup_dialogs` обязан быть
    последним. `message_manager` подменяет отправку (`MockMessageManager` во flow-тестах);
    возвращённая фабрика нужна сценариям фоновой доставки.
    """
    if container is not None:
        setup_dishka(container, dp)
    return setup_dialogs(dp, message_manager=message_manager)
