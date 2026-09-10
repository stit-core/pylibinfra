"""Интеграционные тесты auditkit/pgstore против реального Postgres."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime

import asyncpg
import pytest
import pytest_asyncio
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.auditkit import AuditError, Entry, InvalidEntryError, Logger
from pylibinfra.auditkit.pgstore import MIGRATIONS, AuditPgstoreProvider, Store
from pylibinfra.pgkit import DB, Transactor, migrate_down, migrate_up_all, migrate_version
from pylibinfra.testkit import PostgresFixture, start_postgres

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pg() -> AsyncIterator[PostgresFixture]:
    async with start_postgres(migrations=[MIGRATIONS]) as fixture:
        yield fixture


@pytest_asyncio.fixture(loop_scope="module")
async def db(pg: PostgresFixture) -> DB:
    return await pg.db()


async def test_log_persists_all_fields(db: DB) -> None:
    store = Store(db)
    at = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
    await store.log(
        Entry(
            action="user.create",
            resource="user",
            actor_id="actor-1",
            resource_id="u-42",
            metadata={"ip": "127.0.0.1", "n": 7},
            at=at,
            ip="127.0.0.1",
            request_id="req-xyz",
        )
    )

    row = await db.fetchrow(
        "SELECT actor_id, action, resource, resource_id, metadata::text, ip, request_id, at "
        "FROM audit_log WHERE request_id = $1",
        "req-xyz",
    )
    assert row is not None
    assert row["actor_id"] == "actor-1"
    assert row["action"] == "user.create"
    assert row["resource"] == "user"
    assert row["resource_id"] == "u-42"
    assert row["ip"] == "127.0.0.1"
    assert json.loads(row["metadata"]) == {"ip": "127.0.0.1", "n": 7}
    assert row["at"] == at


async def test_log_nulls_empty_optionals(db: DB) -> None:
    await Store(db).log(Entry(action="login", resource="session", request_id="req-null"))
    row = await db.fetchrow(
        "SELECT actor_id, resource_id, metadata, ip FROM audit_log WHERE request_id = $1",
        "req-null",
    )
    assert row is not None
    assert row["actor_id"] is None
    assert row["resource_id"] is None
    assert row["metadata"] is None
    assert row["ip"] is None


async def test_missing_at_uses_database_clock(db: DB) -> None:
    before = await db.fetchval("SELECT NOW()")
    await Store(db).log(Entry(action="login", resource="session", request_id="req-clock"))
    after = await db.fetchval("SELECT NOW()")

    at = await db.fetchval("SELECT at FROM audit_log WHERE request_id = $1", "req-clock")
    assert before <= at <= after


async def test_log_joins_transaction_from_context(pg: PostgresFixture, db: DB) -> None:
    tr: Transactor = await pg.transactor()
    store = Store(db)

    async def rolled_back() -> None:
        await store.log(Entry(action="x", resource="u", request_id="req-rollback"))
        raise ValueError("domain failed")

    with pytest.raises(ValueError, match="domain failed"):
        await tr.with_tx(rolled_back)
    assert await _count(db, "req-rollback") == 0

    async def committed() -> None:
        await store.log(Entry(action="x", resource="u", request_id="req-commit"))

    await tr.with_tx(committed)
    assert await _count(db, "req-commit") == 1


async def test_log_outside_transaction_is_autocommit(db: DB) -> None:
    await Store(db).log(Entry(action="x", resource="u", request_id="req-auto"))
    assert await _count(db, "req-auto") == 1


async def test_invalid_entry_never_reaches_database(db: DB) -> None:
    with pytest.raises(InvalidEntryError):
        await Store(db).log(Entry(action="", resource="u", request_id="req-invalid"))
    assert await _count(db, "req-invalid") == 0


async def test_insert_error_is_prefixed(db: DB, read_metric: Callable[..., float]) -> None:
    store = Store(db)
    with pytest.raises(AuditError, match="auditkit: insert"):
        # NUL-байт Postgres не принимает в text — настоящая ошибка базы.
        await store.log(Entry(action="x", resource="u", actor_id="a\x00b"))
    assert read_metric("audit_log_writes_total", {"result": "error"}) == 1


async def test_metrics_count_successful_write(db: DB, read_metric: Callable[..., float]) -> None:
    store = Store(db)
    await store.log(Entry(action="x", resource="u", request_id="req-metric"))
    assert read_metric("audit_log_writes_total", {"result": "ok"}) == 1
    assert read_metric("audit_log_write_duration_seconds", {"result": "ok"}) == 1


async def test_migration_0002_creates_valid_at_index(db: DB) -> None:
    valid = await db.fetchval(
        "SELECT i.indisvalid FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid "
        "WHERE c.relname = 'audit_log_at_idx'"
    )
    assert valid is True
    assert await db.fetchval("SELECT count(*) FROM schema_migrations_auditkit") == 2


async def test_migrations_are_idempotent(pg: PostgresFixture, db: DB) -> None:
    # Повторный прогон источника кита (включая CONCURRENTLY-миграцию) — no-op.
    await asyncio.to_thread(migrate_up_all, pg.dsn, MIGRATIONS, timeout=30.0)

    assert await db.fetchval("SELECT count(*) FROM schema_migrations_auditkit") == 2
    assert await db.fetchval("SELECT to_regclass('audit_log_at_idx') IS NOT NULL") is True


async def test_migrations_roll_back(pg: PostgresFixture, db: DB) -> None:
    # Откат сносит схему целиком, поэтому идёт в отдельной базе того же контейнера.
    await db.execute("CREATE DATABASE audit_rollback")
    dsn = f"{pg.dsn.rsplit('/', 1)[0]}/audit_rollback"
    await asyncio.to_thread(migrate_up_all, dsn, MIGRATIONS, timeout=30.0)
    assert await asyncio.to_thread(migrate_version, dsn, MIGRATIONS) == 2

    conn = await asyncpg.connect(dsn)
    try:
        # 0002 несёт `-- transactional: false`, и директива действует на оба
        # направления: DROP INDEX CONCURRENTLY идёт вне транзакционного блока.
        await asyncio.to_thread(migrate_down, dsn, MIGRATIONS)
        assert await conn.fetchval("SELECT to_regclass('audit_log_at_idx') IS NULL") is True
        assert await conn.fetchval("SELECT to_regclass('audit_log') IS NOT NULL") is True

        await asyncio.to_thread(migrate_down, dsn, MIGRATIONS)
        assert await conn.fetchval("SELECT to_regclass('audit_log') IS NULL") is True
        assert await conn.fetchval("SELECT to_regproc('audit_log_no_mutate') IS NULL") is True
        assert await asyncio.to_thread(migrate_version, dsn, MIGRATIONS) == 0
    finally:
        await conn.close()


async def test_audit_log_is_append_only(db: DB) -> None:
    await Store(db).log(Entry(action="login", resource="session", request_id="req-append"))
    with pytest.raises(asyncpg.exceptions.RaiseError):
        await db.execute("UPDATE audit_log SET action = 'x' WHERE request_id = $1", "req-append")
    with pytest.raises(asyncpg.exceptions.RaiseError):
        await db.execute("DELETE FROM audit_log WHERE request_id = $1", "req-append")
    assert await _count(db, "req-append") == 1


async def test_provider_graph_builds(pg: PostgresFixture) -> None:
    pool_db = await pg.db()

    class AppProvider(Provider):
        scope = Scope.APP

        @provide
        def db(self) -> DB:
            return pool_db

    container = make_async_container(AuditPgstoreProvider(), AppProvider())
    try:
        logger = await container.get(Logger)
        assert isinstance(logger, Store)
        await logger.log(Entry(action="x", resource="u", request_id="req-di"))
        assert await _count(pool_db, "req-di") == 1
    finally:
        await container.close()


async def _count(db: DB, request_id: str) -> int:
    return int(
        await db.fetchval("SELECT count(*) FROM audit_log WHERE request_id = $1", request_id)
    )
