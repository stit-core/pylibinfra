"""Сниппет быстрого старта из README кита работает против живого NATS."""

from __future__ import annotations

import asyncio

import pytest
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import logkit, natskit
from pylibinfra.testkit import NatsFixture

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


class AppConfigs(Provider):
    scope = Scope.APP

    @provide
    def log(self) -> logkit.Config:
        return logkit.new_config()

    @provide
    def nats(self) -> natskit.Config:
        return natskit.new_config()  # NATS_*


async def run() -> list[dict[str, int]]:
    container = make_async_container(
        logkit.LogkitProvider(),
        natskit.NatsProvider(),  # Client + Conn + Bus + StreamBus
        AppConfigs(),
    )
    bus = await container.get(natskit.Bus)
    seen: list[dict[str, int]] = []
    delivered = asyncio.Event()

    async def on_order(msg: dict[str, int]) -> None:
        seen.append(msg)
        delivered.set()

    # Subscription позволяет остановить подписку раньше close(); хранить не обязательно.
    await natskit.subscribe_json(bus, "orders.created", on_order)
    await natskit.publish_json(bus, "orders.created", {"id": 42})

    await asyncio.wait_for(delivered.wait(), 5)
    await container.close()  # drain подписок → ожидание обработчиков → close
    return seen


async def test_readme_quickstart(nats_server: NatsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NATS_URL", nats_server.url)
    monkeypatch.setenv("LOG_SILENT", "true")

    assert await run() == [{"id": 42}]
