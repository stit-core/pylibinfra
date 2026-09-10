"""Сниппет быстрого старта из README кита работает против живых Postgres и NATS."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import clockkit, logkit, natskit, outboxkit, pgkit, timerkit
from pylibinfra.outboxkit import pgstore
from pylibinfra.testkit import NatsFixture, PostgresFixture, start_nats, start_postgres

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


class AppConfigs(Provider):
    scope = Scope.APP

    @provide
    def log(self) -> logkit.Config:
        return logkit.new_config()

    @provide
    def pool(self) -> pgkit.PoolConfig:
        return pgkit.new_pool_config()

    @provide
    def migrate(self) -> pgkit.MigrateRunnerConfig:
        return pgkit.new_migrate_runner_config()

    @provide
    def migrations(self) -> pgkit.MigrationSources:
        return (pgstore.MIGRATIONS,)  # миграции кита в общий прогон

    @provide
    def nats(self) -> natskit.Config:
        return natskit.new_config()

    @provide
    def outbox(self) -> outboxkit.Config:
        return outboxkit.new_config()  # OUTBOX_*

    @provide
    def outbox_store(self) -> pgstore.Config:
        return pgstore.new_config()  # OUTBOX_RETENTION_MONTHS, OUTBOX_REQUIRE_TX

    @provide
    def jobs(self, outbox: outboxkit.Outbox) -> timerkit.Jobs:
        return tuple(outbox.jobs())  # drain + партиции + retention


async def run() -> None:
    container = make_async_container(
        logkit.LogkitProvider(),
        pgkit.PgProvider(),
        pgkit.PgMigrateProvider(),
        natskit.NatsProvider(),
        pgstore.OutboxPgstoreProvider(),  # Store как outboxkit.Repository + партиции
        outboxkit.OutboxProvider(),  # Bus, Publisher, Worker, job'ы
        clockkit.ClockProvider(),
        timerkit.TimerkitProvider(),  # гоняет Outbox.job()
        AppConfigs(),
    )
    publisher = await container.get(outboxkit.Publisher)
    transactor = await container.get(pgkit.Transactor)

    async def create_order() -> None:
        # публикация — в той же транзакции, что и доменная запись
        await publisher.publish("order.created", {"id": 42})

    await transactor.with_tx(create_order)
    await container.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pg() -> AsyncIterator[PostgresFixture]:
    async with start_postgres() as fixture:
        yield fixture


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def nats() -> AsyncIterator[NatsFixture]:
    async with start_nats(jetstream=True) as fixture:
        yield fixture


async def test_readme_quickstart(
    pg: PostgresFixture, nats: NatsFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PG_DSN", pg.dsn)
    monkeypatch.setenv("PG_MIGRATIONS_PATH", "")  # своих миграций у теста нет
    monkeypatch.setenv("NATS_URL", nats.url)
    monkeypatch.setenv("NATS_JETSTREAM_ENABLED", "true")
    monkeypatch.setenv("NATS_JS_STREAM_NAMES", "order")
    monkeypatch.setenv("LOG_SILENT", "true")

    await run()

    pool = await pg.pool()
    row = await pool.fetchrow("SELECT subject, payload::text FROM outbox")
    assert row is not None
    assert row["subject"] == "order.created"
    assert row["payload"] == '{"id": 42}'
