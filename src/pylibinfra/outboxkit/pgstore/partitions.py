"""Месячные RANGE-партиции outbox: DDL, создание вперёд и retention через DETACH+DROP.

Имя таблицы и партиции инлайнится в SQL (объект нельзя передать плейсхолдером) —
оно валидируется regex'ом на входе `Store`, а имя партиции проходит `partition_month`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pylibinfra.clockkit import Clock, Real
from pylibinfra.outboxkit.errors import OutboxError
from pylibinfra.pgkit import DB, in_tx


class PendingRowsError(OutboxError):
    """Retention пропустил партиции с неопубликованными событиями; они перечислены в тексте."""


def schema_sql(table: str) -> str:
    """DDL партиционированной таблицы outbox и partial-индекса по неопубликованным."""
    return f"""CREATE TABLE IF NOT EXISTS {table} (
    id              BIGSERIAL    NOT NULL,
    subject         TEXT         NOT NULL,
    payload         JSONB        NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    published_at    TIMESTAMPTZ,
    attempts        INT          NOT NULL DEFAULT 0,
    last_error      TEXT,
    next_attempt_at TIMESTAMPTZ,
    dead_at         TIMESTAMPTZ,
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

CREATE INDEX IF NOT EXISTS {table}_unpublished_idx
    ON {table} (created_at, id) WHERE published_at IS NULL AND dead_at IS NULL;"""


def month_start(moment: datetime) -> datetime:
    """Первый день месяца в UTC — арифметика месяцев идёт только от него."""
    utc = moment.astimezone(UTC)
    return datetime(utc.year, utc.month, 1, tzinfo=UTC)


def add_months(moment: datetime, months: int) -> datetime:
    """Сдвигает начало месяца на `months` (может быть отрицательным)."""
    total = moment.year * 12 + (moment.month - 1) + months
    return datetime(total // 12, total % 12 + 1, 1, tzinfo=UTC)


def partition_sql(table: str, month: datetime) -> tuple[str, str]:
    """Имя месячной партиции и DDL её создания; `month` усекается до начала месяца."""
    start = month_start(month)
    end = add_months(start, 1)
    name = f"{table}_{start.year:04d}_{start.month:02d}"
    sql = (
        f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {table} "
        f"FOR VALUES FROM ('{start:%Y-%m-%d}') TO ('{end:%Y-%m-%d}')"
    )
    return name, sql


def partition_month(table: str, name: str) -> datetime | None:
    """Парсит `<table>_YYYY_MM` в месяц; `None` для чужого формата."""
    prefix = f"{table}_"
    if not name.startswith(prefix):
        return None
    try:
        return datetime.strptime(name[len(prefix) :], "%Y_%m").replace(tzinfo=UTC)
    except ValueError:
        return None


async def ensure_partitions(
    db: DB, table: str, months_ahead: int, clock: Clock | None = None
) -> None:
    """Создаёт партиции текущего месяца и `months_ahead` следующих; идемпотентна."""
    base = month_start((clock or Real()).now())
    for i in range(max(months_ahead, 0) + 1):
        month = add_months(base, i)
        _, sql = partition_sql(table, month)
        try:
            await db.execute(sql)
        except Exception as exc:
            raise OutboxError(
                f"outboxkit: ensure partition {month:%Y-%m} of {table}: {exc}"
            ) from exc


# Берём и партиции родителя, и сирот: успевший DETACH с сорвавшимся DROP иначе
# навсегда выпадает из retention.
_LIST_PARTITIONS_SQL = """WITH parent AS (SELECT to_regclass($1) AS oid)
SELECT c.relname AS name, true AS attached, i.inhdetachpending AS detach_pending
FROM pg_inherits i
JOIN pg_class c ON c.oid = i.inhrelid
WHERE i.inhparent = (SELECT oid FROM parent)
UNION ALL
SELECT c.relname AS name, false AS attached, false AS detach_pending
FROM pg_class c, parent p
WHERE p.oid IS NOT NULL
  AND c.oid <> p.oid
  AND c.relkind IN ('r', 'p')
  AND c.relnamespace = (SELECT relnamespace FROM pg_class WHERE oid = p.oid)
  AND c.relname ~ ('^' || $1 || '_[0-9]{4}_[0-9]{2}$')
  AND NOT EXISTS (SELECT 1 FROM pg_inherits x WHERE x.inhrelid = c.oid)"""


async def drop_old_partitions(
    db: DB, table: str, keep_months: int, clock: Clock | None = None
) -> None:
    """Дропает партиции старше `keep_months` месяцев; `<= 0` — no-op.

    Партиция с неопубликованными (не-dead) событиями остаётся, такие партиции
    перечисляются в `PendingRowsError`. Вызывается вне транзакции: `DETACH …
    CONCURRENTLY` (Postgres ≥ 14) недопустим в transaction block.
    """
    if keep_months <= 0:
        return
    if in_tx():
        raise OutboxError("outboxkit: drop_old_partitions must run outside a transaction")
    cutoff = add_months(month_start((clock or Real()).now()), -keep_months)

    pending: list[str] = []
    for name, attached, detach_pending in await _old_partitions(db, table, cutoff):
        if await _has_pending(db, name):
            pending.append(name)
            continue
        if attached:
            await _detach(db, table, name, detach_pending=detach_pending)
        try:
            # Имя прошло partition_month — это валидный `<table>_YYYY_MM`.
            await db.execute(f"DROP TABLE IF EXISTS {name}")
        except Exception as exc:
            raise OutboxError(f"outboxkit: drop partition {name}: {exc}") from exc
    if pending:
        raise PendingRowsError(
            f"outboxkit: partition holds unpublished events: {', '.join(pending)}"
        )


async def _old_partitions(db: DB, table: str, cutoff: datetime) -> list[tuple[str, bool, bool]]:
    try:
        rows = await db.fetch(_LIST_PARTITIONS_SQL, table)
    except Exception as exc:
        raise OutboxError(f"outboxkit: list partitions of {table}: {exc}") from exc
    out: list[tuple[str, bool, bool]] = []
    for row in rows:
        name = str(row["name"])
        month = partition_month(table, name)
        if month is None or month >= cutoff:
            continue
        out.append((name, bool(row["attached"]), bool(row["detach_pending"])))
    return out


async def _has_pending(db: DB, partition: str) -> bool:
    sql = (
        f"SELECT EXISTS (SELECT 1 FROM {partition} WHERE published_at IS NULL AND dead_at IS NULL)"
    )
    try:
        return bool(await db.fetchval(sql))
    except Exception as exc:
        raise OutboxError(f"outboxkit: check partition {partition}: {exc}") from exc


async def _detach(db: DB, table: str, partition: str, *, detach_pending: bool) -> None:
    """Отцепляет партицию без ACCESS EXCLUSIVE на родителе — DROP выстроил бы очередь вставок."""
    mode = "FINALIZE" if detach_pending else "CONCURRENTLY"
    try:
        await db.execute(f"ALTER TABLE {table} DETACH PARTITION {partition} {mode}")
    except Exception as exc:
        raise OutboxError(f"outboxkit: detach partition {partition}: {exc}") from exc
