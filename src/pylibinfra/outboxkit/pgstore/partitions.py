"""DDL и месячные RANGE-партиции outbox. Зеркалит
golibinfra/outboxkit/pgstore/partitions.go."""

from datetime import UTC, datetime

from pylibinfra.pgkit import DB

_SCHEMA_SQL = """CREATE TABLE IF NOT EXISTS outbox (
    id           BIGSERIAL    NOT NULL,
    subject      TEXT         NOT NULL,
    payload      JSONB        NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    published_at TIMESTAMPTZ,
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

CREATE INDEX IF NOT EXISTS outbox_unpublished_idx
    ON outbox (created_at, id) WHERE published_at IS NULL;"""


def schema_sql() -> str:
    return _SCHEMA_SQL


def _add_months(d: datetime, months: int) -> datetime:
    total = (d.year * 12 + (d.month - 1)) + months
    return datetime(total // 12, total % 12 + 1, 1, tzinfo=UTC)


def partition_sql(month: datetime) -> tuple[str, str]:
    start = datetime(month.year, month.month, 1, tzinfo=UTC)
    end = _add_months(start, 1)
    name = f"outbox_{start.year:04d}_{start.month:02d}"
    sql = (
        f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF outbox "
        f"FOR VALUES FROM ('{start:%Y-%m-%d}') TO ('{end:%Y-%m-%d}')"
    )
    return name, sql


async def ensure_partitions(db: DB, months_ahead: int) -> None:
    """Создаёт партиции outbox для текущего месяца и далее months_ahead вперёд.
    Идемпотентна."""
    months_ahead = max(months_ahead, 0)
    now = datetime.now(UTC)
    base = datetime(now.year, now.month, 1, tzinfo=UTC)
    for i in range(months_ahead + 1):
        _, sql = partition_sql(_add_months(base, i))
        await db.execute(sql)
