import datetime

import pytest
from aiogram import Dispatcher
from aiogram.filters import CommandStart
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Message
from aiogram_dialog import Dialog, DialogManager, StartMode, Window
from aiogram_dialog.widgets.text import Const

from pylibinfra.testkit.bottest import (
    BotClient,
    JsonMemoryStorage,
    MockMessageManager,
    bind_dialogs,
)


async def test_json_storage_rejects_non_serializable() -> None:
    storage = JsonMemoryStorage()
    key = StorageKey(bot_id=1, chat_id=1, user_id=1)
    await storage.set_data(key, {"draft": "ok"})  # сериализуемое — проходит
    with pytest.raises(TypeError):
        await storage.set_data(key, {"when": datetime.datetime.now()})


class _SG(StatesGroup):
    main = State()


async def test_dialog_flow_with_botclient() -> None:
    dialog = Dialog(Window(Const("Hello!"), state=_SG.main))

    async def start(message: Message, dialog_manager: DialogManager) -> None:
        await dialog_manager.start(_SG.main, mode=StartMode.RESET_STACK)

    dp = Dispatcher(storage=JsonMemoryStorage())
    dp.include_router(dialog)
    dp.message.register(start, CommandStart())
    mm = MockMessageManager()
    bind_dialogs(dp, message_manager=mm)

    client = BotClient(dp)
    await client.send("/start")

    mm.assert_one_message()
    assert mm.last_message().text == "Hello!"
