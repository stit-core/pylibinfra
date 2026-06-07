import json
from datetime import UTC, datetime
from typing import Any, cast

from pylibinfra.auditkit import Entry
from pylibinfra.auditkit.pgstore import PgStore
from pylibinfra.pgkit import DB


class CapturingDB:
    """Перехватывает execute(), чтобы проверить маппинг Entry → SQL-аргументы
    без живого Postgres."""

    def __init__(self) -> None:
        self.sql: str | None = None
        self.args: tuple[Any, ...] = ()

    async def execute(self, sql: str, *args: Any) -> str:
        self.sql = sql
        self.args = args
        return "INSERT 0 1"


def new_store() -> tuple[PgStore, CapturingDB]:
    db = CapturingDB()
    return PgStore(cast(DB, db)), db


async def test_record_maps_entry_to_columns_in_order() -> None:
    store, db = new_store()
    at = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
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


async def test_record_nulls_empty_strings_and_metadata() -> None:
    store, db = new_store()
    await store.record(Entry(action="login", resource="session"))

    actor_id, _action, _resource, resource_id, meta, ip, req_id, _at = db.args
    assert actor_id is None
    assert resource_id is None
    assert meta is None
    assert ip is None
    assert req_id is None


async def test_record_defaults_at_to_now_when_unset() -> None:
    store, db = new_store()
    before = datetime.now(UTC)
    await store.record(Entry(action="login", resource="session"))
    after = datetime.now(UTC)

    got_at = db.args[7]
    assert isinstance(got_at, datetime)
    assert before <= got_at <= after
