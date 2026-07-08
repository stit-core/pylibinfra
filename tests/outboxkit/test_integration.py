from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from testcontainers.postgres import PostgresContainer

from pylibinfra.outboxkit import Failure
from pylibinfra.outboxkit.pgstore import PgStore, migrations_path, new_store_for, schema_sql
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
        await pool.execute("TRUNCATE outbox")  # type: ignore[attr-defined]
        await store.insert("user.created", {"foo": "bar", "n": 7})

        msgs = await store.list_unpublished(100)
        assert len(msgs) == 1
        assert msgs[0].subject == "user.created"
        assert json.loads(msgs[0].payload) == {"foo": "bar", "n": 7}
        assert msgs[0].attempts == 0

        await store.mark_published([msgs[0].id])
        assert await store.list_unpublished(100) == []
    finally:
        await pool.close()  # type: ignore[attr-defined]


async def test_require_tx_rejects_outside_tx(pg_dsn: str) -> None:
    migrate_up(MigrateConfig(dsn=pg_dsn, path=migrations_path()))
    pool = await new_pool(PoolConfig(dsn=pg_dsn, min_conns=1, max_conns=3))
    store = PgStore(DB(pool), require_tx=True)
    tr = PoolTransactor(pool)
    try:
        await pool.execute("TRUNCATE outbox")  # type: ignore[attr-defined]
        with pytest.raises(RuntimeError, match="require_tx"):
            await store.insert("evt", {"n": 1})

        async def ok() -> None:
            await store.insert("evt", {"n": 2})

        await tr.with_tx(ok)
        assert len(await store.list_unpublished(10)) == 1
    finally:
        await pool.close()  # type: ignore[attr-defined]


async def test_poison_and_backlog(pg_dsn: str) -> None:
    store, _, pool = await _store(pg_dsn)
    try:
        await pool.execute("TRUNCATE outbox")  # type: ignore[attr-defined]
        await store.insert("evt", {"n": 1})
        await store.insert("evt", {"n": 2})

        msgs = await store.list_unpublished(10)
        assert len(msgs) == 2

        # одно — dead, другое — транзиентный фейл.
        await store.record_failures(
            [
                Failure(id=msgs[0].id, err="boom", dead=True),
                Failure(id=msgs[1].id, err="transient", dead=False),
            ]
        )

        left = await store.list_unpublished(10)
        assert len(left) == 1
        assert left[0].id == msgs[1].id
        assert left[0].attempts == 1  # dead исключён из выборки

        backlog = await store.backlog()
        assert len(backlog) == 1
        assert backlog[0].subject == "evt"
        assert backlog[0].pending == 1
        assert backlog[0].oldest > 0
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

        assert await store.list_unpublished(100) == []

        async def ok() -> None:
            await store.insert("user.created", {"foo": "commit"})

        await tr.with_tx(ok)
        assert len(await store.list_unpublished(100)) == 1
    finally:
        await pool.close()  # type: ignore[attr-defined]


async def test_custom_table(pg_dsn: str) -> None:
    migrate_up(MigrateConfig(dsn=pg_dsn, path=migrations_path()))
    pool = await new_pool(PoolConfig(dsn=pg_dsn, min_conns=1, max_conns=3))
    db = DB(pool)
    try:
        await pool.execute(schema_sql("outbox_orders"))  # type: ignore[attr-defined]
        store = new_store_for(db, "outbox_orders")
        await store.ensure_partitions(1)

        await store.insert("order.created", {"foo": "x"})
        msgs = await store.list_unpublished(10)
        assert len(msgs) == 1
        assert msgs[0].subject == "order.created"

        await store.mark_published([msgs[0].id])
        assert await store.list_unpublished(10) == []
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
