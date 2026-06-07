from typing import Any, Self

import pytest

from pylibinfra.pgkit import DB, PoolTransactor, conn


class FakeTx:
    def __init__(self, conn: "FakeConn") -> None:
        self._conn = conn

    async def __aenter__(self) -> Self:
        self._conn.tx_open = True
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._conn.tx_committed = exc_type is None
        self._conn.tx_rolled_back = exc_type is not None
        self._conn.tx_open = False


class FakeConn:
    def __init__(self) -> None:
        self.tx_open = False
        self.tx_committed = False
        self.tx_rolled_back = False
        self.calls: list[tuple[str, str]] = []

    def transaction(self) -> FakeTx:
        return FakeTx(self)

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append(("execute", sql))
        return "OK"

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.calls.append(("fetchval", sql))
        return 1


class FakeAcquire:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn
        self.acquired = 0

    def acquire(self) -> FakeAcquire:
        self.acquired += 1
        return FakeAcquire(self._conn)

    async def execute(self, sql: str, *args: Any) -> str:
        self._conn.calls.append(("pool.execute", sql))
        return "POOL"


def _pool() -> tuple[FakePool, FakeConn]:
    c = FakeConn()
    return FakePool(c), c


async def test_conn_returns_pool_outside_tx() -> None:
    pool, _ = _pool()
    assert conn(pool) is pool  # type: ignore[arg-type]


async def test_with_tx_routes_to_connection() -> None:
    pool, c = _pool()
    tr = PoolTransactor(pool)  # type: ignore[arg-type]

    async def fn() -> str:
        # внутри tx conn(pool) обязан вернуть tx-соединение, не пул
        assert conn(pool) is c  # type: ignore[arg-type]
        return await conn(pool).execute("INSERT")

    result = await tr.with_tx(fn)
    assert result == "OK"
    assert c.tx_committed
    assert not c.tx_rolled_back
    assert c.calls == [("execute", "INSERT")]


async def test_with_tx_rolls_back_on_error() -> None:
    pool, c = _pool()
    tr = PoolTransactor(pool)  # type: ignore[arg-type]

    async def fn() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await tr.with_tx(fn)
    assert c.tx_rolled_back
    assert not c.tx_committed


async def test_with_tx_nested_reuses_connection() -> None:
    pool, c = _pool()
    tr = PoolTransactor(pool)  # type: ignore[arg-type]

    async def inner() -> None:
        assert conn(pool) is c  # type: ignore[arg-type]

    async def outer() -> None:
        await tr.with_tx(inner)

    await tr.with_tx(outer)
    # вложенный with_tx переиспользует соединение — acquire ровно один раз
    assert pool.acquired == 1


async def test_contextvar_cleared_after_tx() -> None:
    pool, _ = _pool()
    tr = PoolTransactor(pool)  # type: ignore[arg-type]

    async def fn() -> None:
        pass

    await tr.with_tx(fn)
    assert conn(pool) is pool  # type: ignore[arg-type]


async def test_db_uses_pool_outside_tx() -> None:
    pool, c = _pool()
    db = DB(pool)  # type: ignore[arg-type]
    out = await db.execute("SELECT 1")
    assert out == "POOL"
    assert ("pool.execute", "SELECT 1") in c.calls


async def test_db_uses_tx_conn_inside_tx() -> None:
    pool, c = _pool()
    db = DB(pool)  # type: ignore[arg-type]
    tr = PoolTransactor(pool)  # type: ignore[arg-type]

    async def fn() -> None:
        await db.fetchval("SELECT 2")

    await tr.with_tx(fn)
    assert ("fetchval", "SELECT 2") in c.calls
