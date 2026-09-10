"""Store — `outboxkit.Repository` поверх `pgkit.DB`: тик под `FOR UPDATE`, backlog и retention.

Имя таблицы инлайнится в SQL (объект нельзя передать плейсхолдером), поэтому
валидируется regex'ом на входе — ruff S608 по этому файлу гасится в pyproject.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime

from pylibinfra.clockkit import Clock, Real
from pylibinfra.outboxkit.config import PublishMode
from pylibinfra.outboxkit.errors import BusyError, ConfigError, NoTxError, OutboxError
from pylibinfra.outboxkit.metrics import SubjectBacklog
from pylibinfra.outboxkit.outbox import Failure, Message, MessageRef, ReviveFilter
from pylibinfra.pgkit import DB, in_tx, is_lock_not_available
from pylibinfra.timerkit import Job

from .partitions import drop_old_partitions, ensure_partitions

DEFAULT_TABLE = "outbox"
"""Таблица single-instance пути — её заводят миграции пакета."""

KEEPER_INTERVAL = 24 * 3600.0
"""Период job'ов обслуживания: партиции и retention идемпотентны, чаще суток не нужно."""

# Имя таблицы инлайнится в SQL — сужаем его до безопасного идентификатора.
_TABLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

_COLUMNS = (
    "id, created_at, subject, payload, attempts, "
    "(next_attempt_at IS NULL OR next_attempt_at <= NOW()) AS due"
)


class Store:
    """Postgres-реализация `Repository` поверх одной outbox-таблицы."""

    def __init__(
        self,
        db: DB,
        table: str = DEFAULT_TABLE,
        *,
        require_tx: bool = True,
        retention_months: int = 0,
        clock: Clock | None = None,
    ) -> None:
        if not _TABLE_NAME_RE.match(table):
            raise ConfigError(f"outboxkit: invalid table name {table!r}")
        self._db = db
        self._table = table
        self._require_tx = require_tx
        self._retention_months = retention_months
        self._clock = clock or Real()

        self._insert_sql = (
            f"INSERT INTO {table} (subject, payload, created_at) VALUES ($1, $2, NOW())"
        )
        # batch пропускает строки, ждущие backoff; ordered их не перескакивает
        # (порядок важнее пропускной способности) и отсекает хвост в коде.
        self._list_skip_sql = (
            f"SELECT {_COLUMNS} FROM {table}\n"
            "WHERE published_at IS NULL AND dead_at IS NULL\n"
            "    AND (next_attempt_at IS NULL OR next_attempt_at <= NOW())\n"
            "ORDER BY created_at, id LIMIT $1 FOR UPDATE SKIP LOCKED"
        )
        self._list_head_sql = (
            f"SELECT {_COLUMNS} FROM {table}\n"
            "WHERE published_at IS NULL AND dead_at IS NULL\n"
            "ORDER BY created_at, id LIMIT $1 FOR UPDATE NOWAIT"
        )
        # Диапазон created_at — ключ партиционирования: без него UPDATE идёт по всем партициям.
        self._mark_sql = (
            f"UPDATE {table} SET published_at = NOW()\n"
            "WHERE id = ANY($1::bigint[]) AND created_at >= $2 AND created_at <= $3"
        )
        self._fail_sql = (
            f"UPDATE {table} o SET\n"
            "    attempts = o.attempts + 1,\n"
            "    last_error = f.err,\n"
            "    next_attempt_at = CASE WHEN f.retry_after > 0"
            " THEN NOW() + make_interval(secs => f.retry_after) END,\n"
            "    dead_at = CASE WHEN f.dead THEN NOW() ELSE o.dead_at END\n"
            "FROM unnest($1::bigint[], $2::text[], $3::bool[], $4::float8[])"
            " AS f(id, err, dead, retry_after)\n"
            "WHERE o.id = f.id AND o.created_at >= $5 AND o.created_at <= $6"
        )
        self._revive_sql = (
            f"UPDATE {table} SET\n"
            "    dead_at = NULL,\n"
            "    attempts = 0,\n"
            "    last_error = NULL,\n"
            "    next_attempt_at = NULL\n"
            "WHERE dead_at IS NOT NULL\n"
            "    AND (cardinality($1::bigint[]) = 0 OR id = ANY($1::bigint[]))\n"
            "    AND ($2::text = '' OR subject = $2)\n"
            "    AND ($3::timestamptz IS NULL OR created_at >= $3)"
        )
        self._backlog_sql = (
            "SELECT subject,\n"
            "    EXTRACT(EPOCH FROM (NOW() - MIN(created_at)))::float8 AS lag_seconds,\n"
            "    COUNT(*) AS pending\n"
            f"FROM {table}\n"
            "WHERE published_at IS NULL AND dead_at IS NULL\n"
            "GROUP BY subject"
        )

    @property
    def table(self) -> str:
        """Имя outbox-таблицы этого store'а."""
        return self._table

    async def insert(self, subject: str, payload: bytes) -> None:
        """Пишет событие в таблицу; вне транзакции при `require_tx` — `NoTxError`."""
        if self._require_tx and not in_tx():
            raise NoTxError
        if not payload:
            raise OutboxError(f"outboxkit: insert {subject!r}: empty payload")
        try:
            await self._db.execute(self._insert_sql, subject, payload.decode())
        except Exception as exc:
            raise OutboxError(f"outboxkit: insert: {exc}") from exc

    async def list_unpublished(self, limit: int, mode: PublishMode) -> list[Message]:
        """Запирает выбранные строки до конца транзакции вызывающего (`FOR UPDATE`).

        `batch` пропускает занятые (`SKIP LOCKED`), `ordered` на занятой голове
        очереди отдаёт `BusyError` (`NOWAIT`). Строки в backoff'е не выдаются.
        """
        sql = self._list_head_sql if mode == PublishMode.ORDERED else self._list_skip_sql
        try:
            rows = await self._db.fetch(sql, limit)
        except Exception as exc:
            if is_lock_not_available(exc):
                raise BusyError from exc
            raise OutboxError(f"outboxkit: list: {exc}") from exc

        msgs: list[Message] = []
        for row in rows:
            if not row["due"]:
                break
            payload = row["payload"]
            msgs.append(
                Message(
                    id=int(row["id"]),
                    created_at=row["created_at"],
                    subject=str(row["subject"]),
                    payload=payload.encode() if isinstance(payload, str) else bytes(payload),
                    attempts=int(row["attempts"]),
                )
            )
        return msgs

    async def mark_published(self, refs: Sequence[MessageRef]) -> None:
        """Проставляет `published_at` строкам батча по их партициям."""
        if not refs:
            return
        ids, since, until = _ref_bounds(refs)
        try:
            await self._db.execute(self._mark_sql, ids, since, until)
        except Exception as exc:
            raise OutboxError(f"outboxkit: mark published: {exc}") from exc

    async def record_failures(self, failures: Sequence[Failure]) -> None:
        """Одним UPDATE: `attempts`, `last_error`, пауза `next_attempt_at` и `dead_at`."""
        if not failures:
            return
        ids, since, until = _ref_bounds([f.ref for f in failures])
        errs = [f.err for f in failures]
        dead = [f.dead for f in failures]
        retry = [float(f.retry_after) for f in failures]
        try:
            await self._db.execute(self._fail_sql, ids, errs, dead, retry, since, until)
        except Exception as exc:
            raise OutboxError(f"outboxkit: record failures: {exc}") from exc

    async def revive_dead(self, filter: ReviveFilter) -> int:
        """Возвращает dead-строки в очередь (attempts/last_error/backoff сбрасываются)."""
        ids = [int(i) for i in filter.ids]
        try:
            tag = await self._db.execute(self._revive_sql, ids, filter.subject, filter.since)
        except Exception as exc:
            raise OutboxError(f"outboxkit: revive dead: {exc}") from exc
        return _rows_affected(tag)

    async def backlog(self) -> list[SubjectBacklog]:
        """Возраст самого старого и число неопубликованных событий по каждому subject'у."""
        try:
            rows = await self._db.fetch(self._backlog_sql)
        except Exception as exc:
            raise OutboxError(f"outboxkit: backlog: {exc}") from exc
        return [
            SubjectBacklog(
                subject=str(row["subject"]),
                oldest=float(row["lag_seconds"]),
                pending=int(row["pending"]),
            )
            for row in rows
        ]

    async def ensure_partitions(self, months_ahead: int) -> None:
        """Создаёт партиции текущего и следующих `months_ahead` месяцев; идемпотентна."""
        await ensure_partitions(self._db, self._table, months_ahead, self._clock)

    async def drop_old_partitions(self, keep_months: int) -> None:
        """Дропает партиции старше `keep_months` месяцев (вне транзакции)."""
        await drop_old_partitions(self._db, self._table, keep_months, self._clock)

    def maintenance_jobs(self) -> list[Job]:
        """Job'ы обслуживания хранилища: партиции и (если включён) retention."""
        jobs = [partition_keeper_job(self)]
        if self._retention_months > 0:
            jobs.append(retention_keeper_job(self, self._retention_months))
        return jobs


def partition_keeper_job(store: Store) -> Job:
    """Суточный job, держащий партиции текущего и следующего месяца.

    `run_on_start`: кастомную таблицу multi-instance больше некому подготовить, а
    `CREATE TABLE IF NOT EXISTS` идемпотентен.
    """
    return Job(
        name=f"outboxkit.partition-keeper.{store.table}",
        interval=KEEPER_INTERVAL,
        run=lambda: store.ensure_partitions(1),
        run_on_start=True,
    )


def retention_keeper_job(store: Store, keep_months: int) -> Job:
    """Суточный job retention: дропает партиции старше `keep_months` (`<= 0` — без работы)."""
    return Job(
        name=f"outboxkit.retention.{store.table}",
        interval=KEEPER_INTERVAL,
        run=lambda: store.drop_old_partitions(keep_months),
    )


def _ref_bounds(refs: Sequence[MessageRef]) -> tuple[list[int], datetime, datetime]:
    """id'шники батча и границы ключа партиционирования по ним."""
    ids = [r.id for r in refs]
    since = min(r.created_at for r in refs)
    until = max(r.created_at for r in refs)
    return ids, since, until


def _rows_affected(tag: str) -> int:
    """Число строк из command tag asyncpg (`UPDATE 3`)."""
    parts = tag.split()
    if not parts or not parts[-1].isdigit():
        return 0
    return int(parts[-1])
