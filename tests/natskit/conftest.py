"""Общий NATS-контейнер (с JetStream) на весь прогон тестов кита."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio

from pylibinfra.testkit import NatsFixture, start_nats


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def nats_server() -> AsyncIterator[NatsFixture]:
    """Один контейнер на прогон: изоляцию дают уникальные subject'ы и имена стримов."""
    async with start_nats(jetstream=True) as fixture:
        yield fixture
