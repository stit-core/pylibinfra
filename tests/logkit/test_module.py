from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest
from dishka import Provider, Scope, make_async_container, provide
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor

from pylibinfra.logkit import (
    Config,
    ConfigError,
    Logger,
    LogkitProvider,
    OptionalLoggerProvider,
    OptionalRedactConfig,
    RedactConfig,
)


class Configs(Provider):
    scope = Scope.APP

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self._cfg = cfg

    @provide
    def config(self) -> Config:
        return self._cfg


class Redact(Provider):
    scope = Scope.APP

    @provide
    def redact(self) -> OptionalRedactConfig:
        return RedactConfig(enabled=True)


class Sink(Provider):
    scope = Scope.APP

    def __init__(self, provider: LoggerProvider) -> None:
        super().__init__()
        self._provider = provider

    @provide
    def sink(self) -> OptionalLoggerProvider:
        return self._provider


@pytest.fixture
def exporter() -> Iterator[tuple[LoggerProvider, InMemoryLogRecordExporter]]:
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    yield provider, exporter
    provider.shutdown()


async def test_provider_builds_logger(capsys: pytest.CaptureFixture[str]) -> None:
    container = make_async_container(LogkitProvider(), Configs(Config()))

    log = await container.get(Logger)
    log.info("wired", ok=True)
    await container.close()

    assert json.loads(capsys.readouterr().out.strip())["ok"] is True


async def test_optional_dependencies_default_to_off(capsys: pytest.CaptureFixture[str]) -> None:
    container = make_async_container(LogkitProvider(), Configs(Config()))

    log = await container.get(Logger)
    log.info("x", email="john@example.com")
    await container.close()

    assert json.loads(capsys.readouterr().out.strip())["email"] == "john@example.com"


async def test_redact_override_wins(capsys: pytest.CaptureFixture[str]) -> None:
    container = make_async_container(LogkitProvider(), Configs(Config()), Redact())

    log = await container.get(Logger)
    log.info("x", email="john@example.com")
    await container.close()

    assert json.loads(capsys.readouterr().out.strip())["email"] == "[REDACTED]"


async def test_logger_provider_override_adds_otel_sink(
    exporter: tuple[LoggerProvider, InMemoryLogRecordExporter],
) -> None:
    provider, records = exporter
    container = make_async_container(LogkitProvider(), Configs(Config(silent=True)), Sink(provider))

    log = await container.get(Logger)
    log.info("to otlp")
    await container.close()

    assert records.get_finished_logs()[0].log_record.body == "to otlp"


async def test_provider_routes_stdlib_logging(capsys: pytest.CaptureFixture[str]) -> None:
    container = make_async_container(LogkitProvider(), Configs(Config()))

    await container.get(Logger)
    logging.getLogger("uvicorn").error("third party")
    await container.close()

    assert json.loads(capsys.readouterr().out.strip())["logger"] == "uvicorn"


async def test_invalid_config_fails_the_graph() -> None:
    broken = Config.model_construct(level="nope", format="json", source=False, silent=True)
    container = make_async_container(LogkitProvider(), Configs(broken))

    with pytest.raises(ConfigError):
        await container.get(Logger)
    await container.close()
