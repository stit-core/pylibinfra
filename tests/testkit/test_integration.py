"""Integration-тесты testcontainers-обёрток (start_postgres/start_redis/
start_nats) и txkit.in_rollback_tx. Не гоняются в волновом gate — помечены
@pytest.mark.integration (нужен Docker).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from pylibinfra.cachekit import get_json, set_json
from pylibinfra.testkit import start_nats, start_postgres, start_redis
from pylibinfra.testkit.txkit import in_rollback_tx

pytestmark = pytest.mark.integration


async def test_start_postgres_db() -> None:
    async with start_postgres() as pg:
        assert pg.dsn.startswith("postgresql://test:test@")
        async with pg.db() as db:
            val = await db.fetchval("SELECT 1")
            assert val == 1


async def test_in_rollback_tx_rolls_back_writes() -> None:
    import asyncpg  # type: ignore[import-untyped]

    from pylibinfra.pgkit import DB

    async with start_postgres() as pg:
        pool = await asyncpg.create_pool(dsn=pg.dsn)
        try:
            await pool.execute("CREATE TABLE t2 (id int)")
            async with in_rollback_tx(pool):
                # DB через contextvar видит tx-conn, поставленный in_rollback_tx.
                db = DB(pool)
                await db.execute("INSERT INTO t2 (id) VALUES (1)")
                inside = await db.fetchval("SELECT count(*) FROM t2")
                assert inside == 1
            after = await pool.fetchval("SELECT count(*) FROM t2")
            assert after == 0
        finally:
            await pool.close()


async def test_start_redis_cache() -> None:
    async with start_redis() as rds:
        assert rds.url.startswith("redis://")
        async with rds.cache() as cache:
            await set_json(cache, "k", {"v": 1}, timedelta(minutes=1))
            got: tuple[dict[str, int] | None, bool] = await get_json(cache, "k")
            value, found = got
            assert found is True
            assert value == {"v": 1}


async def test_start_nats_bus() -> None:
    async with start_nats() as ns:
        assert ns.url.startswith("nats://")
        async with ns.bus() as bus:
            received: list[bytes] = []

            async def handler(data: bytes) -> None:
                received.append(data)

            await bus.subscribe("test.subject", handler)
            await bus.publish("test.subject", {"hello": "world"})
            await asyncio.sleep(0.2)
            assert received == [b'{"hello":"world"}']
