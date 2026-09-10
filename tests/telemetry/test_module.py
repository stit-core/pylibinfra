from __future__ import annotations

import json
import logging

import pytest
from dishka import Provider, Scope, make_async_container, provide
from dishka.exceptions import GraphMissingFactoryError
from opentelemetry import trace
from opentelemetry._logs import LoggerProvider

from pylibinfra import logkit, telemetry


class Configs(Provider):
    scope = Scope.APP

    def __init__(self, cfg: telemetry.Config, log_cfg: logkit.Config) -> None:
        super().__init__()
        self._cfg = cfg
        self._log_cfg = log_cfg

    @provide
    def telemetry_config(self) -> telemetry.Config:
        return self._cfg

    @provide
    def log_config(self) -> logkit.Config:
        return self._log_cfg


class Redact(Provider):
    scope = Scope.APP

    @provide
    def redact(self) -> logkit.OptionalRedactConfig:
        return logkit.RedactConfig(enabled=True)


def container(*extra: Provider, silent: bool = True):
    return make_async_container(
        logkit.LogkitProvider(),
        telemetry.TelemetryProvider(),
        *extra,
        Configs(telemetry.Config(disabled=True), logkit.Config(silent=silent)),
    )


async def test_providers_are_resolved_and_globals_set() -> None:
    box = container()

    providers = await box.get(telemetry.Providers)

    assert trace.get_tracer_provider() is providers.tracer_provider
    await box.close()


async def test_start_resolves_providers() -> None:
    box = container()

    providers = await telemetry.start(box)

    assert providers is await box.get(telemetry.Providers)
    await box.close()


async def test_start_is_idempotent() -> None:
    # Повторный вызов на уже поднятом графе не строит второй SDK и не вешает
    # второй shutdown: dishka кеширует ресурс APP-scope'а.
    box = container()

    first = await telemetry.start(box)
    second = await telemetry.start(box)

    assert first is second
    await box.close()


async def test_logger_provider_is_exposed() -> None:
    box = container()

    providers = await box.get(telemetry.Providers)

    assert await box.get(LoggerProvider) is providers.logger_provider
    await box.close()


async def test_logkit_picks_up_the_otel_sink() -> None:
    box = container()

    providers = await box.get(telemetry.Providers)
    sink = await box.get(logkit.OptionalLoggerProvider)

    assert sink is providers.logger_provider
    assert isinstance(await box.get(logkit.Logger), object)
    await box.close()


async def test_shutdown_runs_on_container_close() -> None:
    box = container()
    providers = await box.get(telemetry.Providers)
    stopped: list[str] = []
    providers._stop_tracer = lambda _: stopped.append("tracer")  # type: ignore[method-assign]
    providers._stop_meter = lambda _: stopped.append("meter")  # type: ignore[method-assign]
    providers._stop_logger = lambda _: stopped.append("logger")  # type: ignore[method-assign]

    await box.close()

    assert stopped == ["tracer", "meter", "logger"]


async def test_error_logger_writes_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    box = container(silent=False)

    await box.get(telemetry.Providers)
    logging.getLogger("opentelemetry.exporter").error("collector unreachable")
    await box.close()

    record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert record["msg"] == "collector unreachable"


async def test_error_logger_applies_redact(capsys: pytest.CaptureFixture[str]) -> None:
    box = container(Redact(), silent=False)

    await box.get(telemetry.Providers)
    logging.getLogger("opentelemetry.exporter").error("no user john@example.com")
    await box.close()

    record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert record["msg"] == "no user [REDACTED]"


async def test_graph_needs_a_logkit_config() -> None:
    class OnlyTelemetry(Provider):
        scope = Scope.APP

        @provide
        def cfg(self) -> telemetry.Config:
            return telemetry.Config(disabled=True)

    # Граф валидируется на сборке контейнера — как fx.ValidateApp, а не на первом резолве.
    with pytest.raises(GraphMissingFactoryError, match=r"logkit\.config\.Config"):
        make_async_container(telemetry.TelemetryProvider(), OnlyTelemetry())


async def test_tracer_and_meter_are_not_provided() -> None:
    from opentelemetry.metrics import Meter
    from opentelemetry.trace import Tracer

    box = container()

    for kind in (Tracer, Meter):
        with pytest.raises(Exception, match="Cannot find factory"):
            await box.get(kind)
    await box.close()
