"""Сниппет быстрого старта из README кита — закреплён рабочим прогоном."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.auditkit import Entry, Logger
from pylibinfra.auditkit.pgstore import MIGRATIONS, AuditPgstoreProvider
from pylibinfra.pgkit import (
    DB,
    Migrated,
    MigrateRunnerConfig,
    MigrationSources,
    PgMigrateProvider,
    PgProvider,
    PoolConfig,
    Transactor,
    new_migrate_runner_config,
    new_pool_config,
)
from pylibinfra.testkit import PostgresFixture, start_postgres

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


class UserService:
    def __init__(self, db: DB, transactor: Transactor, audit: Logger) -> None:
        self._db = db
        self._tx = transactor
        self._audit = audit

    async def create(self, actor_id: str, user_id: str) -> None:
        # Запись аудита — в той же транзакции, что и доменное изменение.
        async def body() -> None:
            await self._db.execute("INSERT INTO users (id) VALUES ($1)", user_id)
            await self._audit.log(
                Entry(
                    action="user.create",
                    resource="user",
                    actor_id=actor_id,
                    resource_id=user_id,
                    metadata={"source": "admin-ui"},
                    ip="203.0.113.7",
                    request_id="req-abc",
                )
            )

        await self._tx.with_tx(body)


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def pool_config(self) -> PoolConfig:
        return new_pool_config()

    @provide
    def migrate_config(self) -> MigrateRunnerConfig:
        return new_migrate_runner_config()

    @provide
    def migration_sources(self) -> MigrationSources:
        return (MIGRATIONS,)  # создаёт таблицу audit_log

    @provide
    def user_service(self, db: DB, transactor: Transactor, audit: Logger) -> UserService:
        return UserService(db, transactor, audit)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pg() -> AsyncIterator[PostgresFixture]:
    async with start_postgres() as fixture:
        yield fixture


async def test_readme_quickstart(pg: PostgresFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_DSN", pg.dsn)
    monkeypatch.setenv("PG_MIGRATIONS_PATH", "")

    container = make_async_container(
        PgProvider(), PgMigrateProvider(), AuditPgstoreProvider(), AppProvider()
    )
    try:
        await container.get(Migrated)
        users = await container.get(UserService)

        db = await container.get(DB)
        await db.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY)")
        await users.create("admin-1", "u-42")

        row = await db.fetchrow(
            "SELECT action, resource_id FROM audit_log WHERE request_id = $1", "req-abc"
        )
        assert row is not None
        assert row["action"] == "user.create"
        assert row["resource_id"] == "u-42"
    finally:
        await container.close()
