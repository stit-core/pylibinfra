"""Сниппеты README кита должны работать: quick-start собирается, вариант без DI стартует."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import redis.asyncio as redis
from aiogram import Bot, Dispatcher
from aiogram.fsm.state import State, StatesGroup
from aiogram_dialog import Dialog, Window
from aiogram_dialog.widgets.text import Const
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import healthkit, httpkit, logkit, tgbotkit
from pylibinfra.clockkit import ClockProvider
from pylibinfra.testkit.bottest import MemoryRedis


class Menu(StatesGroup):
    main = State()


main_dialog = Dialog(Window(Const("Меню"), state=Menu.main))


class AppProvider(Provider):
    """App-Provider из README; `redis.Redis` в проде даёт `cachekit.CachekitProvider`."""

    scope = Scope.APP

    @provide
    def bot_config(self) -> tgbotkit.Config:
        return tgbotkit.new_config()

    @provide
    def http_config(self) -> httpkit.Config:
        return httpkit.new_config()

    @provide
    def log_config(self) -> logkit.Config:
        return logkit.new_config()

    @provide
    def client(self) -> redis.Redis:
        return MemoryRedis()  # type: ignore[return-value]

    @provide
    def routers(self) -> tgbotkit.RouterBundle:
        return (main_dialog,)

    @provide
    def probes(self, bot: Bot, client: redis.Redis) -> healthkit.Probes:
        return (tgbotkit.probe(bot), tgbotkit.redis_probe(client))


def _container() -> Any:
    return make_async_container(
        tgbotkit.BotProvider(),
        httpkit.HttpProvider(autostart=False),
        logkit.LogkitProvider(),
        ClockProvider(),
        AppProvider(),
    )


@pytest.fixture(autouse=True)
def _bot_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:AAstub-token-for-tests")
    monkeypatch.setenv("HTTP_ADDR", "127.0.0.1:0")
    monkeypatch.setenv("LOG_SILENT", "true")


async def test_quick_start_container_assembles() -> None:
    container = _container()
    try:
        runner = await container.get(tgbotkit.BotRunner)
        assert runner is not None
        assert main_dialog in (await container.get(Dispatcher)).sub_routers
        assert [p.name for p in await container.get(healthkit.Probes)] == [
            "tgbotkit",
            "tgbotkit/redis",
        ]
        assert (await container.get(httpkit.Server)).autostart is False
    finally:
        await container.close()


async def test_no_di_snippet_runs_and_stops() -> None:
    dp = Dispatcher()
    polling = asyncio.Event()

    async def start_polling(*args: Any, **kwargs: Any) -> None:
        await polling.wait()

    async def stop_polling() -> None:
        polling.set()

    dp.start_polling = start_polling  # type: ignore[method-assign]
    dp.stop_polling = stop_polling  # type: ignore[method-assign]

    log = logkit.nop()
    bot = Bot("123456:AAstub-token-for-tests")
    server = httpkit.Server(httpkit.new_config(), log, autostart=False)
    runner = tgbotkit.build_runner(
        cfg=tgbotkit.new_config(),
        bot=bot,
        dp=dp,
        container=make_async_container(),
        client=MemoryRedis(),  # type: ignore[arg-type]
        server=server,
        log=log,
    )
    task = asyncio.create_task(runner.run())
    try:
        for _ in range(200):
            if server.addr():
                break
            await asyncio.sleep(0.01)
        assert server.addr()
    finally:
        runner.stop()
        await asyncio.wait_for(task, timeout=10)
        await bot.session.close()
