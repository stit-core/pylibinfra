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
