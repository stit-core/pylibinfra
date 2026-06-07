import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from testcontainers.postgres import PostgresContainer

from pylibinfra.pgkit import (
    DB,
    MigrateConfig,
    PoolConfig,
    PoolTransactor,
    is_unique_violation,
    migrate_up,
    new_pool,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pg_dsn() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine") as pg:
        host = pg.get_container_host_ip()
        port = pg.get_exposed_port(5432)
        dsn = f"postgresql://{pg.username}:{pg.password}@{host}:{port}/{pg.dbname}"
        yield dsn


@pytest.fixture
def migrations_dir(tmp_path: Path) -> str:
    d = tmp_path / "migrations"
    d.mkdir()
    (d / "0001_create_items.sql").write_text(
        "CREATE TABLE items (id INT PRIMARY KEY, name TEXT UNIQUE NOT NULL);"
    )
    return str(d)


def test_migrate_up_applies_schema(pg_dsn: str, migrations_dir: str) -> None:
    migrate_up(MigrateConfig(dsn=pg_dsn, path=migrations_dir))


async def test_pool_transactor_and_db(pg_dsn: str, migrations_dir: str) -> None:
    os.environ["PG_DSN"] = pg_dsn
    migrate_up(MigrateConfig(dsn=pg_dsn, path=migrations_dir))

    cfg = PoolConfig(dsn=pg_dsn, min_conns=1, max_conns=3)
    pool = await new_pool(cfg)
    try:
        db = DB(pool)
        tr = PoolTransactor(pool)

        async def insert() -> None:
            await db.execute("INSERT INTO items (id, name) VALUES ($1, $2)", 1, "a")

        await tr.with_tx(insert)

        count = await db.fetchval("SELECT count(*) FROM items")
        assert count == 1

        async def insert_dup() -> None:
            await db.execute("INSERT INTO items (id, name) VALUES ($1, $2)", 2, "a")

        with pytest.raises(Exception) as exc_info:  # noqa: PT011
            await tr.with_tx(insert_dup)
        assert is_unique_violation(exc_info.value)

        # rollback после ошибки — строка-дубликат не появилась
        count_after = await db.fetchval("SELECT count(*) FROM items")
        assert count_after == 1
    finally:
        await pool.close()


async def test_session_timeouts_applied(pg_dsn: str) -> None:
    cfg = PoolConfig(dsn=pg_dsn, min_conns=1, max_conns=1, statement_timeout=7.0)
    pool = await new_pool(cfg)
    try:
        got = await pool.fetchval("SHOW statement_timeout")
        assert got == "7s"
    finally:
        await pool.close()
