"""Чистые проверки pgstore без контейнера: имена таблиц и партиций, DDL, источники миграций."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from pylibinfra.outboxkit import ConfigError, OutboxError
from pylibinfra.outboxkit.pgstore import (
    DEFAULT_TABLE,
    KEEPER_INTERVAL,
    MIGRATIONS,
    Store,
    partition_month,
    partition_sql,
    schema_sql,
    table_migrations,
)
from pylibinfra.outboxkit.pgstore.partitions import add_months, month_start
from pylibinfra.pgkit import DB, reset_test_tx, with_test_tx


def _store(table: str = DEFAULT_TABLE, **kwargs: object) -> Store:
    return Store(_FakeDB(), table, **kwargs)  # type: ignore[arg-type]


class _FakeDB:
    async def execute(self, sql: str, *args: object) -> str:
        return "UPDATE 0"

    async def fetch(self, sql: str, *args: object) -> list[object]:
        return []

    async def fetchrow(self, sql: str, *args: object) -> object | None:
        return None

    async def fetchval(self, sql: str, *args: object) -> object:
        return None


@pytest.mark.parametrize(
    "table", ["Outbox", "1outbox", "outbox-orders", "", "outbox; DROP TABLE x"]
)
def test_invalid_table_name_is_rejected(table: str) -> None:
    with pytest.raises(ConfigError, match="invalid table name"):
        _store(table)


def test_table_property_and_maintenance_jobs() -> None:
    store = _store("outbox_orders", retention_months=3)

    assert store.table == "outbox_orders"
    jobs = store.maintenance_jobs()
    assert [job.name for job in jobs] == [
        "outboxkit.partition-keeper.outbox_orders",
        "outboxkit.retention.outbox_orders",
    ]
    assert jobs[0].run_on_start is True
    assert jobs[0].interval == KEEPER_INTERVAL
    # Без retention остаётся только partition-keeper.
    assert len(_store("outbox_orders").maintenance_jobs()) == 1


@pytest.mark.parametrize(
    ("moment", "name", "start", "end"),
    [
        (datetime(2026, 1, 31, 23, 59, tzinfo=UTC), "outbox_2026_01", "2026-01-01", "2026-02-01"),
        (datetime(2026, 12, 1, tzinfo=UTC), "outbox_2026_12", "2026-12-01", "2027-01-01"),
        (datetime(2026, 2, 28, tzinfo=UTC), "outbox_2026_02", "2026-02-01", "2026-03-01"),
    ],
)
def test_partition_sql_names_month_ranges(
    moment: datetime, name: str, start: str, end: str
) -> None:
    got_name, sql = partition_sql(DEFAULT_TABLE, moment)

    assert got_name == name
    assert f"FOR VALUES FROM ('{start}') TO ('{end}')" in sql


def test_month_arithmetic_goes_from_month_start() -> None:
    january = month_start(datetime(2026, 1, 31, 12, tzinfo=UTC))

    assert january == datetime(2026, 1, 1, tzinfo=UTC)
    assert add_months(january, 1) == datetime(2026, 2, 1, tzinfo=UTC)
    assert add_months(january, 12) == datetime(2027, 1, 1, tzinfo=UTC)
    assert add_months(january, -1) == datetime(2025, 12, 1, tzinfo=UTC)


def test_partition_month_parses_only_own_format() -> None:
    assert partition_month("outbox", "outbox_2026_07") == datetime(2026, 7, 1, tzinfo=UTC)
    assert partition_month("outbox", "outbox_orders_2026_07") is None
    assert partition_month("outbox", "other_2026_07") is None
    assert partition_month("outbox", "outbox_2026_13") is None


def test_schema_sql_matches_shipped_migrations() -> None:
    ddl = schema_sql("outbox_orders")
    migration = (Path(MIGRATIONS.path) / "0001_outbox.sql").read_text(encoding="utf-8")
    next_attempt = (Path(MIGRATIONS.path) / "0002_outbox_next_attempt.sql").read_text(
        encoding="utf-8"
    )

    for column in ("id", "subject", "payload", "created_at", "published_at", "attempts"):
        assert column in ddl
        assert column in migration
    assert "next_attempt_at TIMESTAMPTZ" in ddl
    assert "next_attempt_at" in next_attempt
    assert "PARTITION BY RANGE (created_at)" in ddl
    assert "outbox_orders_unpublished_idx" in ddl


def test_migrations_source_is_named_after_the_kit() -> None:
    assert MIGRATIONS.name == "outboxkit"
    assert MIGRATIONS.version_table() == "schema_migrations_outboxkit"
    MIGRATIONS.validate()


def test_table_migrations_generates_own_source() -> None:
    source = table_migrations("outbox_orders")
    source.validate()

    assert source.name == "outbox_orders"
    assert source.version_table() == "schema_migrations_outbox_orders"
    files = sorted(p.name for p in Path(source.path).glob("*.sql"))
    assert files == [
        "0001_outbox_orders.rollback.sql",
        "0001_outbox_orders.sql",
        "0002_outbox_orders_next_attempt.rollback.sql",
        "0002_outbox_orders_next_attempt.sql",
    ]
    body = (Path(source.path) / "0001_outbox_orders.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS outbox_orders" in body
    assert "PARTITION OF outbox_orders" in body
    # Повторный вызов отдаёт тот же источник, а не плодит каталоги.
    assert table_migrations("outbox_orders") is source


def test_table_migrations_rejects_too_long_name() -> None:
    with pytest.raises(ConfigError, match="invalid migrations table name"):
        table_migrations("o" * 46)


async def test_drop_old_partitions_refuses_to_run_inside_transaction() -> None:
    store = _store()
    token = with_test_tx()
    try:
        with pytest.raises(OutboxError, match="outside a transaction"):
            await store.drop_old_partitions(1)
    finally:
        reset_test_tx(token)


async def test_retention_disabled_is_noop() -> None:
    await _store().drop_old_partitions(0)


async def test_insert_rejects_empty_payload() -> None:
    store = _store(require_tx=False)

    with pytest.raises(OutboxError, match="empty payload"):
        await store.insert("order.created", b"")


def test_db_facade_signature_is_the_pgkit_one() -> None:
    # Store ходит только через DB-фасад: сигнатуры зафиксированы pgkit.
    assert hasattr(DB, "execute")
    assert hasattr(DB, "fetch")
    assert hasattr(DB, "fetchval")
