"""Сниппет быстрого старта из README кита — закреплён рабочим прогоном."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import healthkit
from pylibinfra.pgkit import (
    DB,
    Migrated,
    MigrateRunnerConfig,
    MigrationSources,
    OptionalReplicaConfig,
    PgMigrateProvider,
    PgProvider,
    PoolConfig,
    ReadWriteDB,
    Transactor,
    new_migrate_runner_config,
    new_pool_config,
    new_replica_config,
    probe,
    replica_probe,
)
from pylibinfra.testkit import PostgresFixture, start_postgres

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def pool_config(self) -> PoolConfig:
        return new_pool_config()  # PG_DSN и остальные PG_*

    @provide
    def replica_config(self) -> OptionalReplicaConfig:
        return new_replica_config()  # PG_REPLICA_DSN; без реплики провайдер не нужен

    @provide
    def migrate_config(self) -> MigrateRunnerConfig:
        return new_migrate_runner_config()  # PG_MIGRATIONS_PATH, PG_MIGRATE_TIMEOUT

    @provide
    def migration_sources(self) -> MigrationSources:
        return ()  # сюда кладутся <kit>.pgstore.MIGRATIONS

    @provide
    def probes(self, db: DB, rw: ReadWriteDB) -> healthkit.Probes:
        return (probe(db), replica_probe(rw))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pg() -> AsyncIterator[PostgresFixture]:
    async with start_postgres() as fixture:
        yield fixture


async def test_readme_quickstart(
    pg: PostgresFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_users.sql").write_text("CREATE TABLE users (email TEXT PRIMARY KEY);")
    monkeypatch.setenv("PG_DSN", pg.dsn)
    monkeypatch.setenv("PG_MIGRATIONS_PATH", str(migrations))
    monkeypatch.delenv("PG_REPLICA_DSN", raising=False)

    container = make_async_container(PgProvider(), PgMigrateProvider(), AppProvider())

    await container.get(Migrated)  # схема применена до приёма трафика
    db = await container.get(DB)
    transactor = await container.get(Transactor)

    async def create_user(email: str) -> None:
        async def write() -> None:
            await db.execute("INSERT INTO users (email) VALUES ($1)", email)

        await transactor.with_tx(write)  # исключение или отмена → ROLLBACK

    try:
        await create_user("a@b.c")
        assert await db.fetchval("SELECT count(*) FROM users") == 1

        probes = await container.get(healthkit.Probes)
        assert [p.name for p in probes] == ["pgkit", "pgkit/replica"]
        for p in probes:
            await p.run()
    finally:
        await container.close()
