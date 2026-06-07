import json
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from testcontainers.postgres import PostgresContainer

from pylibinfra.auditkit import Entry
from pylibinfra.auditkit.pgstore import PgStore, migrations_path
from pylibinfra.pgkit import DB, MigrateConfig, PoolConfig, migrate_up, new_pool

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pg_dsn() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine") as pg:
        host = pg.get_container_host_ip()
        port = pg.get_exposed_port(5432)
        yield f"postgresql://{pg.username}:{pg.password}@{host}:{port}/{pg.dbname}"


async def test_record_persists_entry(pg_dsn: str) -> None:
    migrate_up(MigrateConfig(dsn=pg_dsn, path=migrations_path()))

    pool = await new_pool(PoolConfig(dsn=pg_dsn, min_conns=1, max_conns=2))
    try:
        db = DB(pool)
        store = PgStore(db)

        at = datetime.now(UTC).replace(microsecond=123456)
        await store.record(
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
            "SELECT actor_id, action, resource, resource_id, metadata::text, "
            "ip, request_id, at FROM audit_log WHERE request_id = $1",
            "req-xyz",
        )
        assert row is not None
        assert row["actor_id"] == "actor-1"
        assert row["action"] == "user.create"
        assert row["resource"] == "user"
        assert row["resource_id"] == "u-42"
        assert row["ip"] == "127.0.0.1"
        assert row["request_id"] == "req-xyz"
        assert json.loads(row["metadata"]) == {"ip": "127.0.0.1", "n": 7}
        assert row["at"] == at
    finally:
        await pool.close()


async def test_record_nulls_empty_optionals(pg_dsn: str) -> None:
    migrate_up(MigrateConfig(dsn=pg_dsn, path=migrations_path()))

    pool = await new_pool(PoolConfig(dsn=pg_dsn, min_conns=1, max_conns=2))
    try:
        db = DB(pool)
        store = PgStore(db)

        await store.record(Entry(action="login", resource="session", request_id="req-null"))

        row = await db.fetchrow(
            "SELECT actor_id, resource_id, metadata, ip FROM audit_log WHERE request_id = $1",
            "req-null",
        )
        assert row is not None
        assert row["actor_id"] is None
        assert row["resource_id"] is None
        assert row["metadata"] is None
        assert row["ip"] is None
    finally:
        await pool.close()
