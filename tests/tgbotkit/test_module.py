from typing import Any

import pytest
import redis.asyncio as redis
from aiogram import Dispatcher
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.logkit import Logger, nop
from pylibinfra.tgbotkit import BotProvider, Config
from pylibinfra.tgbotkit.runner import PollingRunner, WebhookRunner, build_runner

from .conftest import FakeRedis


class _Deps(Provider):
    scope = Scope.APP

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self._cfg = cfg

    @provide
    def config(self) -> Config:
        return self._cfg

    @provide
    def client(self) -> redis.Redis:
        return FakeRedis()  # type: ignore[return-value]

    @provide
    def logger(self) -> Logger:
        return nop()


async def test_container_builds_dispatcher_with_chain_and_error_handlers() -> None:
    container = make_async_container(BotProvider(), _Deps(Config(token="123:abc")))
    dp = await container.get(Dispatcher)
    assert isinstance(dp, Dispatcher)
    assert len(dp.errors.handlers) >= 1  # register_error_handlers отработал
    await container.close()


def test_build_runner_selects_mode() -> None:
    polling = build_runner(
        cfg=Config(token="123:abc", mode="polling"),
        bot=None,  # type: ignore[arg-type]
        dp=None,  # type: ignore[arg-type]
        container=None,  # type: ignore[arg-type]
        client=FakeRedis(),  # type: ignore[arg-type]
    )
    assert isinstance(polling, PollingRunner)

    webhook = build_runner(
        cfg=Config(token="123:abc", mode="webhook"),
        bot=None,  # type: ignore[arg-type]
        dp=None,  # type: ignore[arg-type]
        container=None,  # type: ignore[arg-type]
        client=FakeRedis(),  # type: ignore[arg-type]
    )
    assert isinstance(webhook, WebhookRunner)


class _FakeBot:
    def __init__(self, fail: bool = False) -> None:
        self._fail = fail

    async def get_me(self) -> str:
        if self._fail:
            raise RuntimeError("bot unreachable")
        return "me"


class _FakeRedisPing:
    async def ping(self) -> bool:
        return True


class _FakeConn:
    def __init__(self, connected: bool = True) -> None:
        self.is_connected = connected

    def healthy(self) -> None:
        if not self.is_connected:
            raise RuntimeError("natskit: not connected")


def _runner(bot: Any, conn: Any) -> PollingRunner:
    return build_runner(
        cfg=Config(token="123:abc", mode="polling"),
        bot=bot,
        dp=None,  # type: ignore[arg-type]
        container=None,  # type: ignore[arg-type]
        client=_FakeRedisPing(),  # type: ignore[arg-type]
        conn=conn,
    )  # type: ignore[return-value]


async def test_probes_include_nats_only_when_conn_present() -> None:
    without = _runner(_FakeBot(), None)
    assert len(without._probes()) == 2  # bot + redis, без nats

    with_conn = _runner(_FakeBot(), _FakeConn())
    assert len(with_conn._probes()) == 3  # bot + redis + nats


async def test_probes_pass_when_healthy() -> None:
    runner = _runner(_FakeBot(), _FakeConn(connected=True))
    for p in runner._probes():
        await p()  # ни один не бросает на здоровых зависимостях


async def test_nats_probe_raises_when_disconnected() -> None:
    runner = _runner(_FakeBot(), _FakeConn(connected=False))
    with pytest.raises(RuntimeError):
        await runner._nats_probe()


async def test_bot_probe_raises_when_unreachable() -> None:
    runner = _runner(_FakeBot(fail=True), None)
    with pytest.raises(RuntimeError):
        await runner._bot_probe()
