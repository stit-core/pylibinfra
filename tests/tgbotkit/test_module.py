"""BotProvider: сборка графа кита и фабрики health-проб."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import redis.asyncio as redis
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.fsm.storage.redis import RedisStorage
from dishka import AsyncContainer, Provider, Scope, make_async_container, provide
from dishka.exceptions import ExitError

from pylibinfra import healthkit, httpkit
from pylibinfra.clockkit import Clock, Real
from pylibinfra.logkit import Logger, nop
from pylibinfra.testkit.bottest import MemoryRedis
from pylibinfra.tgbotkit import (
    PROBE_NAME,
    REDIS_PROBE_NAME,
    BotProvider,
    BotRunner,
    CommandBundle,
    CommandSpec,
    Config,
    RouterBundle,
    TgbotkitError,
    probe,
    redis_probe,
)
from pylibinfra.tgbotkit.middleware import OutgoingThrottleMiddleware

from .conftest import cfg


class Deps(Provider):
    """Минимум, который консьюмер обязан положить в контейнер рядом с `BotProvider`."""

    scope = Scope.APP

    def __init__(self, bot_config: Config, client: Any) -> None:
        super().__init__()
        self._cfg = bot_config
        self._client = client

    @provide
    def bot_config(self) -> Config:
        return self._cfg

    @provide
    def http_config(self) -> httpkit.Config:
        return httpkit.Config(addr=":0")

    @provide
    def client(self) -> redis.Redis:
        return self._client

    @provide
    def logger(self) -> Logger:
        return nop()

    @provide
    def clock(self) -> Clock:
        return Real()


def build_container(bot_config: Config | None = None, client: Any = None) -> AsyncContainer:
    """Контейнер бота: кит, HTTP-сервер без автостарта и зависимости консьюмера."""
    return make_async_container(
        BotProvider(),
        httpkit.HttpProvider(autostart=False),
        Deps(bot_config or cfg(), client or MemoryRedis()),
    )


async def test_container_builds_the_whole_graph() -> None:
    container = build_container()
    try:
        assert isinstance(await container.get(Bot), Bot)
        assert isinstance(await container.get(RedisStorage), RedisStorage)
        assert isinstance(await container.get(Dispatcher), Dispatcher)
        assert await container.get(BotRunner) is not None
    finally:
        await container.close()


async def test_dispatcher_has_kit_chain_and_error_handlers() -> None:
    container = build_container()
    try:
        dp = await container.get(Dispatcher)
        kit = [
            type(mw).__name__
            for mw in dp.update.outer_middleware._middlewares
            if type(mw).__module__.startswith("pylibinfra.")
        ]
        assert kit[0] == "RequestIdMiddleware"
        assert kit[-1] == "ObservabilityMiddleware"
        assert len(dp.errors.handlers) == 1
    finally:
        await container.close()


async def test_consumer_bundles_reach_the_dispatcher() -> None:
    router = Router(name="feature")

    async def start(message: Any) -> None: ...

    class Extra(Provider):
        scope = Scope.APP

        @provide
        def routers(self) -> RouterBundle:
            return (router,)

        @provide
        def commands(self) -> CommandBundle:
            return (CommandSpec("message", start, (Command("start"),)),)

    container = make_async_container(
        BotProvider(),
        httpkit.HttpProvider(autostart=False),
        Deps(cfg(), MemoryRedis()),
        Extra(),
    )
    try:
        dp = await container.get(Dispatcher)
        assert router in dp.sub_routers
        assert len(dp.message.handlers) == 1
    finally:
        await container.close()


async def test_flood_middleware_is_installed_on_the_session() -> None:
    container = build_container()
    try:
        bot = await container.get(Bot)
        assert any(
            isinstance(mw, OutgoingThrottleMiddleware) for mw in bot.session.middleware._middlewares
        )
    finally:
        await container.close()


async def test_flood_middleware_can_be_disabled() -> None:
    container = build_container(cfg(flood_enabled=False))
    try:
        bot = await container.get(Bot)
        assert not [
            mw
            for mw in bot.session.middleware._middlewares
            if isinstance(mw, OutgoingThrottleMiddleware)
        ]
    finally:
        await container.close()


async def test_bot_gets_configured_timeout_and_parse_mode() -> None:
    container = build_container(cfg(api_timeout=12.5, parse_mode="MarkdownV2"))
    try:
        bot = await container.get(Bot)
        assert bot.session.timeout == 12.5
        assert bot.default.parse_mode == "MarkdownV2"
    finally:
        await container.close()


async def test_empty_parse_mode_disables_default_formatting() -> None:
    container = build_container(cfg(parse_mode=""))
    try:
        assert (await container.get(Bot)).default.parse_mode is None
    finally:
        await container.close()


async def test_bot_session_is_closed_even_if_a_neighbour_cleanup_fails() -> None:
    class _Broken(Provider):
        scope = Scope.APP

        @provide
        async def resource(self) -> AsyncIterator[str]:
            yield "open"
            raise RuntimeError("cleanup exploded")

    container = make_async_container(
        BotProvider(),
        httpkit.HttpProvider(autostart=False),
        Deps(cfg(), MemoryRedis()),
        _Broken(),
    )
    bot = await container.get(Bot)
    await container.get(str)
    closed: list[int] = []
    original = bot.session.close

    async def close() -> None:
        closed.append(1)
        await original()

    bot.session.close = close  # type: ignore[method-assign]

    with pytest.raises(ExitError):
        await container.close()

    # Сессия aiohttp не имеет права пережить контейнер из-за чужого сбоя закрытия.
    assert closed == [1]


async def test_state_ttl_reaches_storage() -> None:
    container = build_container(cfg(state_ttl=3600))
    try:
        storage = await container.get(RedisStorage)
        assert (storage.state_ttl, storage.data_ttl) == (3600, 3600)
    finally:
        await container.close()


class _FakeBot:
    def __init__(self, fail: bool = False) -> None:
        self._fail = fail

    async def get_me(self) -> str:
        if self._fail:
            raise RuntimeError("bot unreachable")
        return "me"


async def test_bot_probe_is_named_and_passes() -> None:
    p = probe(_FakeBot())  # type: ignore[arg-type]
    assert p.name == PROBE_NAME == "tgbotkit"
    await p.run()


async def test_bot_probe_prefixes_its_error() -> None:
    with pytest.raises(TgbotkitError, match="tgbotkit: get_me"):
        await probe(_FakeBot(fail=True)).run()  # type: ignore[arg-type]


async def test_redis_probe_is_named_and_passes() -> None:
    p = redis_probe(MemoryRedis())  # type: ignore[arg-type]
    assert p.name == REDIS_PROBE_NAME == "tgbotkit/redis"
    await p.run()


async def test_redis_probe_prefixes_its_error() -> None:
    with pytest.raises(TgbotkitError, match="tgbotkit: redis ping"):
        await redis_probe(MemoryRedis(fail=True)).run()  # type: ignore[arg-type]


async def test_probes_are_collected_by_the_consumer() -> None:
    class Probes(Provider):
        scope = Scope.APP

        @provide
        async def probes(self, bot: Bot, client: redis.Redis) -> AsyncIterator[healthkit.Probes]:
            yield (probe(bot), redis_probe(client))

    container = make_async_container(
        BotProvider(),
        httpkit.HttpProvider(autostart=False),
        Deps(cfg(), MemoryRedis()),
        Probes(),
    )
    try:
        probes = await container.get(healthkit.Probes)
        assert [p.name for p in probes] == ["tgbotkit", "tgbotkit/redis"]
    finally:
        await container.close()
