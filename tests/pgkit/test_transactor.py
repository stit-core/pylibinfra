import asyncio
from typing import Any

import pytest

from pylibinfra.pgkit import (
    DB,
    Executor,
    PoolTransactor,
    TransactionError,
    Transactor,
    TxOptions,
    conn,
    in_tx,
    reset_test_tx,
    with_test_tx,
)


class FakeTx:
    def __init__(self, c: "FakeConn", *, fail_on: str = "") -> None:
        self._conn = c
        self._fail_on = fail_on

    async def start(self) -> None:
        if self._fail_on == "start":
            raise RuntimeError("cannot begin")
        self._conn.events.append("begin")

    async def commit(self) -> None:
        if self._fail_on == "commit":
            raise RuntimeError("cannot commit")
        self._conn.events.append("commit")

    async def rollback(self) -> None:
        if self._fail_on == "rollback":
            raise RuntimeError("cannot rollback")
        self._conn.events.append("rollback")


class FakeConn:
    def __init__(self, fail_on: str = "") -> None:
        self.events: list[str] = []
        self.calls: list[tuple[str, str]] = []
        self.tx_opts: dict[str, Any] = {}
        self._fail_on = fail_on

    def transaction(self, **opts: Any) -> FakeTx:
        self.tx_opts = opts
        return FakeTx(self, fail_on=self._fail_on)

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append(("execute", sql))
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        self.calls.append(("fetch", sql))
        return []

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.calls.append(("fetchrow", sql))
        return None

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.calls.append(("fetchval", sql))
        return 1


class FakeAcquire:
    def __init__(self, c: FakeConn) -> None:
        self._conn = c

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakePool:
    def __init__(self, c: FakeConn) -> None:
        self._conn = c
        self.acquired = 0

    def acquire(self) -> FakeAcquire:
        self.acquired += 1
        return FakeAcquire(self._conn)

    async def execute(self, sql: str, *args: Any) -> str:
        self._conn.calls.append(("pool.execute", sql))
        return "POOL"

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        self._conn.calls.append(("pool.fetch", sql))
        return []

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self._conn.calls.append(("pool.fetchrow", sql))
        return None

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self._conn.calls.append(("pool.fetchval", sql))
        return 0


def _pool(fail_on: str = "") -> tuple[FakePool, FakeConn]:
    c = FakeConn(fail_on)
    return FakePool(c), c


async def test_conn_returns_pool_outside_tx() -> None:
    pool, _ = _pool()
    assert conn(pool) is pool
    assert in_tx() is False


async def test_with_tx_routes_to_connection_and_commits() -> None:
    pool, c = _pool()
    tr: Transactor = PoolTransactor(pool)

    async def fn() -> str:
        assert conn(pool) is c
        assert in_tx() is True
        return await conn(pool).execute("INSERT")

    assert await tr.with_tx(fn) == "OK"
    assert c.events == ["begin", "commit"]
    assert c.calls == [("execute", "INSERT")]
    assert in_tx() is False


async def test_with_tx_rolls_back_on_error() -> None:
    pool, c = _pool()
    tr = PoolTransactor(pool)

    async def fn() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await tr.with_tx(fn)
    assert c.events == ["begin", "rollback"]
    assert in_tx() is False


async def test_with_tx_rolls_back_on_cancel() -> None:
    pool, c = _pool()
    tr = PoolTransactor(pool)

    async def fn() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await tr.with_tx(fn)
    assert c.events == ["begin", "rollback"]
    assert in_tx() is False


async def test_rollback_error_does_not_replace_cause() -> None:
    pool, _ = _pool(fail_on="rollback")
    tr = PoolTransactor(pool)

    async def fn() -> None:
        raise ValueError("real cause")

    with pytest.raises(ValueError, match="real cause") as exc_info:
        await tr.with_tx(fn)
    assert any("pgkit: rollback" in note for note in exc_info.value.__notes__)


async def test_begin_error_is_wrapped() -> None:
    pool, _ = _pool(fail_on="start")
    tr = PoolTransactor(pool)

    async def fn() -> None:  # pragma: no cover — не должен вызываться
        raise AssertionError

    with pytest.raises(TransactionError, match="pgkit: begin tx"):
        await tr.with_tx(fn)


async def test_commit_error_is_wrapped() -> None:
    pool, _ = _pool(fail_on="commit")
    tr = PoolTransactor(pool)

    async def fn() -> None:
        return None

    with pytest.raises(TransactionError, match="pgkit: commit"):
        await tr.with_tx(fn)


async def test_options_are_passed_to_asyncpg() -> None:
    pool, c = _pool()
    tr = PoolTransactor(pool)

    async def fn() -> None:
        return None

    await tr.with_tx(fn, options=TxOptions(isolation="serializable", read_only=True))
    assert c.tx_opts == {"isolation": "serializable", "readonly": True, "deferrable": False}


async def test_nested_with_tx_reuses_connection_and_ignores_options() -> None:
    pool, c = _pool()
    tr = PoolTransactor(pool)

    async def inner() -> None:
        assert conn(pool) is c

    async def outer() -> None:
        await tr.with_tx(inner, options=TxOptions(isolation="serializable"))

    await tr.with_tx(outer)
    assert pool.acquired == 1
    assert c.events == ["begin", "commit"]
    assert c.tx_opts["isolation"] is None


async def test_db_uses_pool_outside_tx() -> None:
    pool, c = _pool()
    db = DB(pool)
    assert await db.execute("SELECT 1") == "POOL"
    assert ("pool.execute", "SELECT 1") in c.calls


async def test_db_uses_tx_conn_inside_tx() -> None:
    pool, c = _pool()
    db = DB(pool)
    tr = PoolTransactor(pool)

    async def fn() -> None:
        await db.fetchval("SELECT 2")
        await db.fetch("SELECT 3")
        await db.fetchrow("SELECT 4")

    await tr.with_tx(fn)
    assert ("fetchval", "SELECT 2") in c.calls
    assert ("fetch", "SELECT 3") in c.calls
    assert ("fetchrow", "SELECT 4") in c.calls


async def test_test_tx_marker_is_seen_by_in_tx_but_not_by_conn() -> None:
    pool, _ = _pool()
    token = with_test_tx()
    try:
        assert in_tx() is True
        assert conn(pool) is pool
    finally:
        reset_test_tx(token)
    assert in_tx() is False


def test_pool_and_conn_satisfy_executor_protocol() -> None:
    pool, c = _pool()
    assert isinstance(pool, Executor)
    assert isinstance(c, Executor)
