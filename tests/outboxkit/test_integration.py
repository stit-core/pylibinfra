from __future__ import annotations

from collections.abc import Iterator

import pytest
from testcontainers.postgres import PostgresContainer

from pylibinfra.outboxkit.pgstore import PgStore, migrations_path
from pylibinfra.pgkit import DB, MigrateConfig, PoolConfig, PoolTransactor, migrate_up, new_pool

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pg_dsn() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine") as pg:
        host = pg.get_container_host_ip()
        port = pg.get_exposed_port(5432)
        yield f"postgresql://{pg.username}:{pg.password}@{host}:{port}/{pg.dbname}"


async def _store(pg_dsn: str) -> tuple[PgStore, PoolTransactor, object]:
    migrate_up(MigrateConfig(dsn=pg_dsn, path=migrations_path()))
    pool = await new_pool(PoolConfig(dsn=pg_dsn, min_conns=1, max_conns=3))
    db = DB(pool)
    return PgStore(db), PoolTransactor(pool), pool


async def test_happy_path(pg_dsn: str) -> None:
    store, _, pool = await _store(pg_dsn)
    try:
        await store.insert("user.created", {"foo": "bar", "n": 7})

        msgs = await store.list_unpublished(100)
        assert len(msgs) == 1
        assert msgs[0].subject == "user.created"
        assert msgs[0].payload == {"foo": "bar", "n": 7}

        await store.mark_published(msgs[0].id)
        assert await store.list_unpublished(100) == []
    finally:
        await pool.close()  # type: ignore[attr-defined]


async def test_rollback_drops_message(pg_dsn: str) -> None:
    store, tr, pool = await _store(pg_dsn)
    try:
        await pool.execute("TRUNCATE outbox")  # type: ignore[attr-defined]

        sentinel = RuntimeError("business failure")

        async def fail() -> None:
            await store.insert("user.created", {"foo": "rollback"})
            raise sentinel

        with pytest.raises(RuntimeError, match="business failure"):
            await tr.with_tx(fail)

        # Insert откатился вместе с tx — outbox пуст.
        assert await store.list_unpublished(100) == []

        async def ok() -> None:
            await store.insert("user.created", {"foo": "commit"})

        await tr.with_tx(ok)
        assert len(await store.list_unpublished(100)) == 1
    finally:
        await pool.close()  # type: ignore[attr-defined]


async def test_table_is_partitioned_and_ensure_idempotent(pg_dsn: str) -> None:
    store, _, pool = await _store(pg_dsn)
    try:
        kind = await pool.fetchval(  # type: ignore[attr-defined]
            "SELECT relkind::text FROM pg_class WHERE relname='outbox'"
        )
        assert kind == "p"

        await store.ensure_partitions(6)
        await store.ensure_partitions(6)
    finally:
        await pool.close()  # type: ignore[attr-defined]
