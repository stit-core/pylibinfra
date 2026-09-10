from __future__ import annotations

import io
import json

from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import logkit, telemetry


class AppConfigs(Provider):
    """App-Provider консьюмера: конфиги регистрирует он, киты — нет."""

    scope = Scope.APP

    @provide
    def log(self) -> logkit.Config:
        return logkit.Config(silent=True)

    @provide
    def redact(self) -> logkit.OptionalRedactConfig:
        return logkit.new_redact_config()

    @provide
    def telemetry_config(self) -> telemetry.Config:
        return telemetry.Config(disabled=True)


async def test_readme_quick_start_with_dishka() -> None:
    container = make_async_container(
        logkit.LogkitProvider(),
        telemetry.TelemetryProvider(),
        AppConfigs(),
    )

    await telemetry.start(container)
    log = await container.get(logkit.Logger)
    log.info("service started")

    await container.close()


async def test_readme_quick_start_without_telemetry() -> None:
    class Configs(Provider):
        scope = Scope.APP

        @provide
        def log(self) -> logkit.Config:
            return logkit.Config(silent=True)

    container = make_async_container(logkit.LogkitProvider(), Configs())

    log = await container.get(logkit.Logger)
    log.info("service started")

    await container.close()


def test_readme_quick_start_without_di() -> None:
    stream = io.StringIO()
    cfg = logkit.new_config()

    log = logkit.new(cfg, writer=stream)
    log.info("service started")

    with_options = logkit.new(
        cfg,
        writer=stream,
        redact=logkit.RedactConfig(enabled=True),
        service=logkit.ServiceConfig(service="api", env="dev", version="v1.2.3"),
    )
    with_options.with_(component="worker").info("login john@example.com", order_id=7)

    record = json.loads(stream.getvalue().splitlines()[-1])
    assert record["msg"] == "login [REDACTED]"
    assert record["service"] == "api"
    assert record["component"] == "worker"
    assert record["order_id"] == 7


def test_readme_register_attr_snippet() -> None:
    stream = io.StringIO()
    tenant = "acme"

    logkit.register_attr("tenant", lambda: tenant or None)
    logkit.new(logkit.Config(), writer=stream).info("processing order")

    assert json.loads(stream.getvalue())["tenant"] == "acme"


def test_readme_enabled_snippet() -> None:
    stream = io.StringIO()
    log = logkit.new(logkit.Config(level="info"), writer=stream)

    if log.enabled("debug"):
        log.debug("payload", body="expensive dump")

    assert stream.getvalue() == ""
