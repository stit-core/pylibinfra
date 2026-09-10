"""Интеграционные тесты pgkit против реального Postgres (один контейнер на модуль)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import asyncpg
import psycopg2
import pytest
import pytest_asyncio
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.pgkit import (
    DB,
    ROLE_PRIMARY,
    AppSourceName,
    ConnectError,
    Migrated,
    MigrateError,
    MigrateRunnerConfig,
    MigrationSources,
    OptionalReplicaConfig,
    PgMigrateProvider,
    PgProvider,
    PoolConfig,
    PoolTransactor,
    ReadWriteDB,
    ReplicaConfig,
    Source,
    Transactor,
    TxOptions,
    instrument_pool,
    is_unique_violation,
    migrate_down,
    migrate_up,
    migrate_up_all,
    migrate_version,
    new_pool,
    probe,
    replica_probe,
)
from pylibinfra.pgkit.migrate import advisory_lock_key
from pylibinfra.testkit import PostgresFixture, start_postgres

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pg() -> AsyncIterator[PostgresFixture]:
    async with start_postgres() as fixture:
        yield fixture


def _source(tmp_path: Path, name: str, up: str, down: str = "", *, version: str = "0001") -> Source:
    directory = tmp_path / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{version}_{name}.sql").write_text(up)
    if down:
        (directory / f"{version}_{name}.rollback.sql").write_text(down)
    return Source(name=name, path=str(directory))


async def test_pool_applies_session_timeouts(pg: PostgresFixture) -> None:
    cfg = pg.pool_config().model_copy(update={"statement_timeout": 7.0, "lock_timeout": 1.5})
    pool = await new_pool(cfg)
    try:
        assert await pool.fetchval("SHOW statement_timeout") == "7s"
        assert await pool.fetchval("SHOW lock_timeout") == "1500ms"
        assert await pool.fetchval("SHOW idle_in_transaction_session_timeout") == "1min"
    finally:
        await pool.close()


async def test_pool_pings_on_start(pg: PostgresFixture) -> None:
    pool = await new_pool(pg.pool_config())
    try:
        assert await pool.fetchval("SELECT 1") == 1
    finally:
        await pool.close()


async def test_pool_reports_connect_failure() -> None:
    cfg = PoolConfig.model_validate(
        {"dsn": "postgresql://nobody@127.0.0.1:1/none", "connect_timeout": 2.0, "min_conns": 1}
    )
    with pytest.raises(ConnectError, match="pgkit: connect"):
        await new_pool(cfg)


async def test_pool_metrics_report_live_pool(
    pg: PostgresFixture, read_metric: Callable[..., float]
) -> None:
    pool = await new_pool(pg.pool_config().model_copy(update={"min_conns": 1, "max_conns": 3}))
    registration = instrument_pool(pool, ROLE_PRIMARY)
    try:
        attrs = {"role": "primary"}
        assert read_metric("pg_pool_max_conns", attrs) == 3
        assert read_metric("pg_pool_total_conns", attrs) >= 1
        assert read_metric("pg_pool_idle_conns", attrs) >= 0
    finally:
        registration.unregister()
        await pool.close()


async def test_transactor_commits_and_rolls_back(pg: PostgresFixture) -> None:
    db = await pg.db()
    tr = await pg.transactor()
    await db.execute("CREATE TABLE IF NOT EXISTS tx_items (id INT PRIMARY KEY, name TEXT UNIQUE)")
    await db.execute("TRUNCATE tx_items")

    async def insert_one() -> None:
        await db.execute("INSERT INTO tx_items (id, name) VALUES (1, 'a')")

    await tr.with_tx(insert_one)
    assert await db.fetchval("SELECT count(*) FROM tx_items") == 1

    async def insert_duplicate() -> None:
        await db.execute("INSERT INTO tx_items (id, name) VALUES (2, 'a')")

    with pytest.raises(asyncpg.PostgresError) as exc_info:
        await tr.with_tx(insert_duplicate)
    assert is_unique_violation(exc_info.value)
    assert await db.fetchval("SELECT count(*) FROM tx_items") == 1


async def test_transactor_rolls_back_on_cancel(pg: PostgresFixture) -> None:
    db = await pg.db()
    tr = await pg.transactor()
    await db.execute("CREATE TABLE IF NOT EXISTS tx_cancel (id INT PRIMARY KEY)")
    await db.execute("TRUNCATE tx_cancel")

    async def body() -> None:
        await db.execute("INSERT INTO tx_cancel (id) VALUES (1)")
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await tr.with_tx(body)
    assert await db.fetchval("SELECT count(*) FROM tx_cancel") == 0


async def test_transactor_read_only_option(pg: PostgresFixture) -> None:
    db = await pg.db()
    tr = await pg.transactor()
    await db.execute("CREATE TABLE IF NOT EXISTS tx_ro (id INT PRIMARY KEY)")

    async def body() -> None:
        await db.execute("INSERT INTO tx_ro (id) VALUES (1)")

    with pytest.raises(asyncpg.PostgresError, match="read-only"):
        await tr.with_tx(body, options=TxOptions(read_only=True))


async def test_transactor_serializable_option(pg: PostgresFixture) -> None:
    db = await pg.db()
    tr = await pg.transactor()

    async def body() -> str:
        return str(await db.fetchval("SHOW transaction_isolation"))

    assert await tr.with_tx(body, options=TxOptions(isolation="serializable")) == "serializable"


async def test_nested_with_tx_shares_one_transaction(pg: PostgresFixture) -> None:
    db = await pg.db()
    tr = await pg.transactor()
    await db.execute("CREATE TABLE IF NOT EXISTS tx_nested (id INT PRIMARY KEY)")
    await db.execute("TRUNCATE tx_nested")

    async def inner() -> None:
        await db.execute("INSERT INTO tx_nested (id) VALUES (2)")

    async def outer() -> None:
        await db.execute("INSERT INTO tx_nested (id) VALUES (1)")
        await tr.with_tx(inner)
        raise ValueError("rollback both")

    with pytest.raises(ValueError, match="rollback both"):
        await tr.with_tx(outer)
    assert await db.fetchval("SELECT count(*) FROM tx_nested") == 0


async def test_migrate_up_version_and_down(pg: PostgresFixture, tmp_path: Path) -> None:
    src = _source(
        tmp_path,
        "solo",
        "CREATE TABLE solo_items (id INT PRIMARY KEY);",
        "DROP TABLE solo_items;",
    )
    assert await asyncio.to_thread(migrate_version, pg.dsn, src) == 0

    await asyncio.to_thread(migrate_up, pg.dsn, src)
    assert await asyncio.to_thread(migrate_version, pg.dsn, src) == 1

    db = await pg.db()
    assert await db.fetchval("SELECT to_regclass('solo_items') IS NOT NULL") is True
    assert await db.fetchval("SELECT to_regclass('schema_migrations_solo') IS NOT NULL") is True

    # Повторный прогон — no-op, а не ошибка.
    await asyncio.to_thread(migrate_up, pg.dsn, src)
    assert await asyncio.to_thread(migrate_version, pg.dsn, src) == 1

    await asyncio.to_thread(migrate_down, pg.dsn, src)
    assert await asyncio.to_thread(migrate_version, pg.dsn, src) == 0
    assert await db.fetchval("SELECT to_regclass('solo_items') IS NULL") is True


async def test_migrate_down_on_empty_source_is_noop(pg: PostgresFixture, tmp_path: Path) -> None:
    src = _source(tmp_path, "nodown", "CREATE TABLE nodown_t (id INT);", "DROP TABLE nodown_t;")
    await asyncio.to_thread(migrate_down, pg.dsn, src)
    assert await asyncio.to_thread(migrate_version, pg.dsn, src) == 0


async def test_sources_keep_independent_version_tables(pg: PostgresFixture, tmp_path: Path) -> None:
    kit = _source(tmp_path, "kitone", "CREATE TABLE kitone_t (id INT PRIMARY KEY);")
    app_dir = tmp_path / "appmig"
    app_dir.mkdir()
    (app_dir / "0001_app.sql").write_text("CREATE TABLE app_t (id INT PRIMARY KEY);")
    app = Source(name=AppSourceName, path=str(app_dir))

    await asyncio.to_thread(migrate_up_all, pg.dsn, kit, app, timeout=30.0)
    db = await pg.db()
    assert await db.fetchval("SELECT to_regclass('schema_migrations_kitone') IS NOT NULL") is True
    assert await db.fetchval("SELECT to_regclass('schema_migrations_app') IS NOT NULL") is True
    assert await asyncio.to_thread(migrate_version, pg.dsn, kit) == 1
    assert await asyncio.to_thread(migrate_version, pg.dsn, app) == 1

    # Новая миграция кита не двигает версию приложения.
    (Path(kit.path) / "0002_kitone.sql").write_text("CREATE TABLE kitone_more (id INT);")
    await asyncio.to_thread(migrate_up_all, pg.dsn, kit, app, timeout=30.0)
    assert await asyncio.to_thread(migrate_version, pg.dsn, kit) == 2
    assert await asyncio.to_thread(migrate_version, pg.dsn, app) == 1


async def test_migrate_up_all_serializes_concurrent_starts(
    pg: PostgresFixture, tmp_path: Path
) -> None:
    src = _source(
        tmp_path,
        "racy",
        "SELECT pg_sleep(0.4);\nCREATE TABLE racy_t (id INT PRIMARY KEY);",
    )
    await asyncio.gather(
        asyncio.to_thread(migrate_up_all, pg.dsn, src, timeout=30.0),
        asyncio.to_thread(migrate_up_all, pg.dsn, src, timeout=30.0),
    )
    db = await pg.db()
    assert await db.fetchval("SELECT to_regclass('racy_t') IS NOT NULL") is True
    assert await asyncio.to_thread(migrate_version, pg.dsn, src) == 1


async def test_migrate_up_all_times_out_waiting_for_lock(
    pg: PostgresFixture, tmp_path: Path
) -> None:
    src = _source(tmp_path, "blocked", "CREATE TABLE blocked_t (id INT);")
    conn = await asyncio.to_thread(psycopg2.connect, pg.dsn)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (advisory_lock_key(),))
        with pytest.raises(MigrateError, match="timed out"):
            await asyncio.to_thread(migrate_up_all, pg.dsn, src, timeout=1.0)
    finally:
        conn.close()


async def test_migrate_up_reports_bad_sql(pg: PostgresFixture, tmp_path: Path) -> None:
    src = _source(tmp_path, "broken", "CREATE TABLE ;")
    with pytest.raises(MigrateError, match="migrate source 'broken'"):
        await asyncio.to_thread(migrate_up, pg.dsn, src)


async def test_read_write_db_and_probes(pg: PostgresFixture) -> None:
    pool = await pg.pool()
    master = DB(pool)
    without_replica = ReadWriteDB(master, None)
    assert without_replica.has_replica() is False
    await probe(master).run()
    await replica_probe(without_replica).run()

    replica_pool = await new_pool(pg.pool_config().model_copy(update={"max_conns": 2}))
    try:
        with_replica = ReadWriteDB(master, DB(replica_pool))
        assert with_replica.has_replica() is True
        await replica_probe(with_replica).run()
        assert await with_replica.read().fetchval("SELECT 1") == 1
    finally:
        await replica_pool.close()


async def test_provider_graph_builds(pg: PostgresFixture, tmp_path: Path) -> None:
    kit = _source(tmp_path, "provkit", "CREATE TABLE provkit_t (id INT PRIMARY KEY);")
    pool_cfg = pg.pool_config().model_copy(update={"max_conns": 2, "min_conns": 1})

    class AppProvider(Provider):
        scope = Scope.APP

        @provide
        def pool_config(self) -> PoolConfig:
            return pool_cfg

        @provide
        def migrate_config(self) -> MigrateRunnerConfig:
            return MigrateRunnerConfig.model_validate(
                {"migrations_path": "", "migrate_timeout": 30.0}
            )

        @provide
        def sources(self) -> MigrationSources:
            return (kit,)

    container = make_async_container(PgProvider(), PgMigrateProvider(), AppProvider())
    try:
        assert isinstance(await container.get(Migrated), Migrated)
        db = await container.get(DB)
        assert await db.fetchval("SELECT to_regclass('provkit_t') IS NOT NULL") is True
        transactor = await container.get(Transactor)
        assert isinstance(transactor, PoolTransactor)
        rw = await container.get(ReadWriteDB)
        assert rw.has_replica() is False
    finally:
        await container.close()


async def test_provider_graph_with_replica(
    pg: PostgresFixture, read_metric: Callable[..., float]
) -> None:
    pool_cfg = pg.pool_config().model_copy(update={"max_conns": 2, "min_conns": 1})

    class AppProvider(Provider):
        scope = Scope.APP

        @provide
        def pool_config(self) -> PoolConfig:
            return pool_cfg

        @provide
        def replica_config(self) -> OptionalReplicaConfig:
            return ReplicaConfig.model_validate({"replica_dsn": pg.dsn, "max_conns": 2})

    container = make_async_container(PgProvider(), AppProvider())
    try:
        rw = await container.get(ReadWriteDB)
        assert rw.has_replica() is True
        assert await rw.read().fetchval("SELECT 1") == 1
        await replica_probe(rw).run()
        assert read_metric("pg_pool_max_conns", {"role": "replica"}) == 2
        assert read_metric("pg_pool_max_conns", {"role": "primary"}) == 2
    finally:
        await container.close()
    # Закрытие контейнера снимает пулы с наблюдения.
    assert read_metric("pg_pool_max_conns", {"role": "replica"}) == 0


async def test_migrate_provider_without_sources_is_noop(pg: PostgresFixture) -> None:
    pool_cfg = pg.pool_config()

    class AppProvider(Provider):
        scope = Scope.APP

        @provide
        def pool_config(self) -> PoolConfig:
            return pool_cfg

        @provide
        def migrate_config(self) -> MigrateRunnerConfig:
            return MigrateRunnerConfig.model_validate(
                {"migrations_path": "", "migrate_timeout": 30.0}
            )

    container = make_async_container(PgMigrateProvider(), AppProvider())
    try:
        assert await container.get(MigrationSources) == ()
        assert isinstance(await container.get(Migrated), Migrated)
    finally:
        await container.close()
