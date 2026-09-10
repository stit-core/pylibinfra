"""Интеграционные тесты testkit.start_postgres и pgtest.in_rollback_tx."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from pylibinfra.pgkit import DB, PoolTransactor, Source, migrate_version
from pylibinfra.testkit import POSTGRES_IMAGE, PostgresFixture, start_postgres
from pylibinfra.testkit.pgtest import in_rollback_tx

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pg() -> AsyncIterator[PostgresFixture]:
    async with start_postgres(image=POSTGRES_IMAGE) as fixture:
        yield fixture


async def test_fixture_exposes_dsn_and_container(pg: PostgresFixture) -> None:
    assert pg.dsn.startswith("postgresql://test:test@")
    assert pg.container is not None


async def test_pool_config_carries_prod_timeouts(pg: PostgresFixture) -> None:
    cfg = pg.pool_config()
    assert cfg.dsn.get_secret_value() == pg.dsn
    assert cfg.statement_timeout == 30.0
    assert cfg.idle_in_tx_session_timeout == 60.0
    assert cfg.lock_timeout == 5.0
    assert cfg.connect_timeout == 10.0


async def test_db_transactor_and_pool_share_one_pool(pg: PostgresFixture) -> None:
    pool = await pg.pool()
    assert await pg.pool() is pool

    db = await pg.db()
    assert await db.fetchval("SELECT 1") == 1

    transactor = await pg.transactor()
    assert isinstance(transactor, PoolTransactor)

    await db.execute("CREATE TABLE IF NOT EXISTS fixture_items (id INT PRIMARY KEY)")
    await db.execute("TRUNCATE fixture_items")

    async def insert() -> None:
        await db.execute("INSERT INTO fixture_items (id) VALUES (1)")

    await transactor.with_tx(insert)
    assert await pool.fetchval("SELECT count(*) FROM fixture_items") == 1


async def test_in_rollback_tx_rolls_back_writes(pg: PostgresFixture) -> None:
    pool = await pg.pool()
    db = DB(pool)
    await db.execute("CREATE TABLE IF NOT EXISTS rollback_items (id INT PRIMARY KEY)")
    await db.execute("TRUNCATE rollback_items")

    async with in_rollback_tx(pool):
        await db.execute("INSERT INTO rollback_items (id) VALUES (1)")
        assert await db.fetchval("SELECT count(*) FROM rollback_items") == 1

    assert await pool.fetchval("SELECT count(*) FROM rollback_items") == 0


async def test_start_postgres_applies_sources_and_app_dir(tmp_path: Path) -> None:
    kit_dir = tmp_path / "kitmig"
    kit_dir.mkdir()
    (kit_dir / "0001_kit.sql").write_text("CREATE TABLE kit_t (id INT PRIMARY KEY);")
    app_dir = tmp_path / "appmig"
    app_dir.mkdir()
    (app_dir / "0001_app.sql").write_text("CREATE TABLE app_t (id INT REFERENCES kit_t(id));")

    kit = Source(name="kitmig", path=str(kit_dir))
    async with start_postgres(migrations=[kit], migrations_dir=str(app_dir)) as pg:
        db = await pg.db()
        assert await db.fetchval("SELECT to_regclass('kit_t') IS NOT NULL") is True
        assert await db.fetchval("SELECT to_regclass('app_t') IS NOT NULL") is True
        assert await db.fetchval("SELECT to_regclass('schema_migrations_app') IS NOT NULL") is True
        assert migrate_version(pg.dsn, kit) == 1
        pool = await pg.pool()

    assert pool.is_closing() is True
