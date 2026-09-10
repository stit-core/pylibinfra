from typing import Any

import pytest

from pylibinfra.pgkit import DB, PgkitError, ReadWriteDB, probe, replica_probe


class FakePool:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.queries: list[str] = []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.queries.append(sql)
        if self.error is not None:
            raise self.error
        return 1


async def test_probe_runs_select_one() -> None:
    pool = FakePool()
    p = probe(DB(pool))
    assert p.name == "pgkit"
    await p.run()
    assert pool.queries == ["SELECT 1"]


async def test_probe_prefixes_error() -> None:
    p = probe(DB(FakePool(error=RuntimeError("down"))))
    with pytest.raises(PgkitError, match="pgkit: ping: down"):
        await p.run()


async def test_replica_probe_without_replica_is_noop() -> None:
    master_pool = FakePool()
    rw = ReadWriteDB(DB(master_pool), None)
    p = replica_probe(rw)
    assert p.name == "pgkit/replica"
    await p.run()
    assert master_pool.queries == []


async def test_replica_probe_queries_replica() -> None:
    replica_pool = FakePool()
    rw = ReadWriteDB(DB(FakePool()), DB(replica_pool))
    await replica_probe(rw).run()
    assert replica_pool.queries == ["SELECT 1"]


async def test_replica_probe_prefixes_error() -> None:
    rw = ReadWriteDB(DB(FakePool()), DB(FakePool(error=RuntimeError("lag"))))
    with pytest.raises(PgkitError, match="pgkit: replica ping: lag"):
        await replica_probe(rw).run()
