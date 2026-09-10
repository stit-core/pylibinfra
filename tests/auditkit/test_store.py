import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from pylibinfra.auditkit import AuditError, Entry, InvalidEntryError, Logger
from pylibinfra.auditkit.pgstore import MIGRATIONS, Store
from pylibinfra.pgkit import DB


class CapturingDB:
    """Перехватывает execute(), чтобы проверить маппинг Entry → SQL-аргументы без Postgres."""

    def __init__(self, error: Exception | None = None) -> None:
        self.sql: str | None = None
        self.args: tuple[Any, ...] = ()
        self.error = error

    async def execute(self, sql: str, *args: Any) -> str:
        self.sql = sql
        self.args = args
        if self.error is not None:
            raise self.error
        return "INSERT 0 1"


def _store(error: Exception | None = None) -> tuple[Store, CapturingDB]:
    db = CapturingDB(error)
    return Store(cast(DB, db)), db


async def test_store_satisfies_logger_port() -> None:
    store, _ = _store()
    assert isinstance(store, Logger)


async def test_log_maps_entry_to_columns_in_order() -> None:
    store, db = _store()
    at = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
    await store.log(
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

    assert db.sql is not None
    assert "INSERT INTO audit_log" in db.sql
    actor_id, action, resource, resource_id, meta, ip, req_id, got_at = db.args
    assert actor_id == "actor-1"
    assert action == "user.create"
    assert resource == "user"
    assert resource_id == "u-42"
    assert json.loads(meta) == {"ip": "127.0.0.1", "n": 7}
    assert ip == "127.0.0.1"
    assert req_id == "req-xyz"
    assert got_at == at


async def test_log_nulls_empty_strings_and_metadata() -> None:
    store, db = _store()
    await store.log(Entry(action="login", resource="session"))
    actor_id, _action, _resource, resource_id, meta, ip, req_id, at = db.args
    assert actor_id is None
    assert resource_id is None
    assert meta is None
    assert ip is None
    assert req_id is None
    # None → COALESCE подставит NOW() базы, а не часы процесса.
    assert at is None
    assert "COALESCE($8, NOW())" in (db.sql or "")


async def test_log_rejects_invalid_entry_before_query(
    read_metric: Callable[..., float],
) -> None:
    store, db = _store()
    with pytest.raises(InvalidEntryError):
        await store.log(Entry(action="", resource="user"))
    assert db.sql is None
    assert read_metric("audit_log_writes_total", {"result": "invalid"}) == 1


async def test_log_rejects_naive_at_before_query(read_metric: Callable[..., float]) -> None:
    store, db = _store()
    with pytest.raises(InvalidEntryError, match="at must be timezone-aware"):
        await store.log(Entry(action="x", resource="u", at=datetime(2026, 6, 7, 12, 0)))
    assert db.sql is None
    assert read_metric("audit_log_writes_total", {"result": "invalid"}) == 1


async def test_log_rejects_unserializable_metadata(read_metric: Callable[..., float]) -> None:
    store, db = _store()
    with pytest.raises(AuditError, match="marshal metadata"):
        await store.log(Entry(action="x", resource="u", metadata={"bad": object()}))
    assert db.sql is None
    assert read_metric("audit_log_writes_total", {"result": "invalid"}) == 1


async def test_log_wraps_insert_error(read_metric: Callable[..., float]) -> None:
    store, _ = _store(error=RuntimeError("connection lost"))
    with pytest.raises(AuditError, match="auditkit: insert: connection lost"):
        await store.log(Entry(action="x", resource="u"))
    assert read_metric("audit_log_writes_total", {"result": "error"}) == 1
    assert read_metric("audit_log_write_duration_seconds", {"result": "error"}) == 1


async def test_successful_log_records_metrics(read_metric: Callable[..., float]) -> None:
    store, _ = _store()
    await store.log(Entry(action="x", resource="u"))
    assert read_metric("audit_log_writes_total", {"result": "ok"}) == 1
    assert read_metric("audit_log_write_duration_seconds", {"result": "ok"}) == 1


def test_migrations_source_is_named_after_kit() -> None:
    assert MIGRATIONS.name == "auditkit"
    assert MIGRATIONS.version_table() == "schema_migrations_auditkit"
    MIGRATIONS.validate()
