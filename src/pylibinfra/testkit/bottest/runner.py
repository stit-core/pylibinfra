"""bind_dialogs — deferred-проводка для тестов (как BotRunner._bind, но с in-memory
event-isolation вместо Redis): опц. setup_dishka(container) + setup_dialogs
(строго последним). message_manager — для MockMessageManager в flow-тестах.
Возвращает BgManagerFactory для фоновых сценариев.
"""

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
    if container is not None:
        setup_dishka(container, dp)
    return setup_dialogs(dp, message_manager=message_manager)
