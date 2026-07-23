"""Проверка dishka-проводки: Provider'ы китов собираются в контейнер,
зависимости резолвятся, lifecycle отрабатывает на close().
"""

import pytest
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.clockkit import Clock, ClockProvider
from pylibinfra.flagkit import Config as FlagConfig
from pylibinfra.flagkit import FlagkitProvider, Store
from pylibinfra.logkit import Config as LogConfig
from pylibinfra.logkit import Logger, LogkitProvider, RedactConfig


class _Configs(Provider):
    scope = Scope.APP

    @provide
    def log(self) -> LogConfig:
        return LogConfig()

    @provide
    def redact(self) -> RedactConfig:
        return RedactConfig()

    @provide
    def flag(self) -> FlagConfig:
        return FlagConfig()


async def test_full_container_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_FLAGS", "x=true")

    container = make_async_container(
        LogkitProvider(),
        ClockProvider(),
        FlagkitProvider(),
        _Configs(),
    )

    logger = await container.get(Logger)
    logger.info("wired", ok=True)

    clock = await container.get(Clock)
    assert clock.now() is not None

    store = await container.get(Store)
    assert store.bool("x") is True

    await container.close()
