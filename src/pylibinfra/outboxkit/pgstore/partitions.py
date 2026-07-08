"""DDL и месячные RANGE-партиции outbox. Зеркалит
golibinfra/outboxkit/pgstore/partitions.go. Имя таблицы/партиции инлайнится в SQL
(валидируется в store.py / прошло partition_month) — ruff S608 гасится в pyproject."""

from datetime import UTC, datetime

from pylibinfra.pgkit import DB


def schema_sql(table: str) -> str:
    """DDL партиционированной таблицы outbox + индекс для заданного имени — для
    bootstrap кастомных таблиц multi-instance (caller заводит свою миграцию).
    DEFAULT_TABLE создаётся миграцией 0001 (+ poison-колонки миграцией 0002)."""
    return f"""CREATE TABLE IF NOT EXISTS {table} (
    id           BIGSERIAL    NOT NULL,
    subject      TEXT         NOT NULL,
    payload      JSONB        NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    published_at TIMESTAMPTZ,
    attempts     INT          NOT NULL DEFAULT 0,
    last_error   TEXT,
    dead_at      TIMESTAMPTZ,
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

CREATE INDEX IF NOT EXISTS {table}_unpublished_idx
    ON {table} (created_at, id) WHERE published_at IS NULL AND dead_at IS NULL;"""


def _add_months(d: datetime, months: int) -> datetime:
    total = (d.year * 12 + (d.month - 1)) + months
    return datetime(total // 12, total % 12 + 1, 1, tzinfo=UTC)


def partition_sql(table: str, month: datetime) -> tuple[str, str]:
    start = datetime(month.year, month.month, 1, tzinfo=UTC)
    end = _add_months(start, 1)
    name = f"{table}_{start.year:04d}_{start.month:02d}"
    sql = (
        f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {table} "
        f"FOR VALUES FROM ('{start:%Y-%m-%d}') TO ('{end:%Y-%m-%d}')"
    )
    return name, sql


async def ensure_partitions(db: DB, table: str, months_ahead: int) -> None:
    """Создаёт партиции outbox для текущего месяца и далее months_ahead вперёд.
    Идемпотентна."""
    months_ahead = max(months_ahead, 0)
    now = datetime.now(UTC)
    base = datetime(now.year, now.month, 1, tzinfo=UTC)
    for i in range(months_ahead + 1):
        _, sql = partition_sql(table, _add_months(base, i))
        await db.execute(sql)


_LIST_PARTITIONS_SQL = """SELECT c.relname AS name FROM pg_inherits i
JOIN pg_class c ON c.oid = i.inhrelid
JOIN pg_class p ON p.oid = i.inhparent
WHERE p.relname = $1"""


async def drop_old_partitions(db: DB, table: str, keep_months: int) -> None:
    """Удаляет партиции, чей месяц старше keep_months месяцев до текущего
    (retention). keep_months <= 0 — no-op. DROP всей партиции дешевле, чем DELETE
    строк — ради этого outbox и партиционирован по месяцам."""
    if keep_months <= 0:
        return
    now = datetime.now(UTC)
    cutoff = _add_months(datetime(now.year, now.month, 1, tzinfo=UTC), -keep_months)

    rows = await db.fetch(_LIST_PARTITIONS_SQL, table)
    drop: list[str] = []
    for r in rows:
        name = r["name"]
        m = partition_month(table, name)
        if m is not None and m < cutoff:
            drop.append(name)

    for name in drop:
        # name прошёл partition_month — это валидный `<table>_YYYY_MM`, безопасно инлайнить.
        await db.execute(f"DROP TABLE IF EXISTS {name}")


def partition_month(table: str, name: str) -> datetime | None:
    """Парсит `<table>_YYYY_MM` → месяц; None для чужого формата."""
    prefix = f"{table}_"
    if not name.startswith(prefix):
        return None
    suffix = name[len(prefix) :]
    try:
        return datetime.strptime(suffix, "%Y_%m").replace(tzinfo=UTC)
    except ValueError:
        return None
