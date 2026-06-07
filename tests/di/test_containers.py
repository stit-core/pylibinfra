"""Проверка dishka-проводки: Provider'ы китов собираются в контейнер,
зависимости резолвятся, lifecycle (telemetry shutdown) отрабатывает на close().
"""

import pytest
from dishka import Provider, Scope, make_async_container, provide
from opentelemetry.metrics import Meter
from opentelemetry.trace import Tracer

from pylibinfra.clockkit import Clock, ClockProvider
from pylibinfra.flagkit import Config as FlagConfig
from pylibinfra.flagkit import FlagkitProvider, Store
from pylibinfra.logkit import Config as LogConfig
from pylibinfra.logkit import Logger, LogkitProvider, RedactConfig
from pylibinfra.telemetry import Config as TelConfig
from pylibinfra.telemetry import TelemetryProvider


class _Configs(Provider):
    scope = Scope.APP

    @provide
    def tel(self) -> TelConfig:
        return TelConfig()

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
    monkeypatch.setenv("OTEL_SERVICE_NAME", "di-test")
    monkeypatch.setenv("OTEL_STDOUT_ONLY", "true")
    monkeypatch.setenv("FEATURE_FLAGS", "x=true")

    container = make_async_container(
        TelemetryProvider(),
        LogkitProvider(),
        ClockProvider(),
        FlagkitProvider(),
        _Configs(),
    )

    # tracer резолвится первым — триггерит telemetry init (выставляет globals,
    # которые читает logkit для OTel-bridge).
    tracer = await container.get(Tracer)
    assert tracer is not None
    meter = await container.get(Meter)
    assert meter is not None

    logger = await container.get(Logger)
    logger.info("wired", ok=True)

    clock = await container.get(Clock)
    assert clock.now() is not None

    store = await container.get(Store)
    assert store.bool("x") is True

    await container.close()
