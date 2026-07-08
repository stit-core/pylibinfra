"""PgStore — реализация outboxkit.Repository поверх pgkit.DB. SQL зеркалит
golibinfra/outboxkit/pgstore/store.go 1:1 (asyncpg-плейсхолдеры $1). Имя таблицы
инлайнится в SQL (объект нельзя передать плейсхолдером), поэтому валидируется
regex'ом на входе — ruff S608 по этому файлу гасится в pyproject."""

from __future__ import annotations

import json
import re
from typing import Any

from pylibinfra.outboxkit.metrics import SubjectBacklog
from pylibinfra.outboxkit.outbox import Failure, Message
from pylibinfra.pgkit import DB, in_tx

from .partitions import drop_old_partitions, ensure_partitions

# DefaultTable — таблица single-instance Module (миграция 0001).
DEFAULT_TABLE = "outbox"

# Ограничивает имя таблицы безопасным идентификатором (инлайнится в SQL).
_TABLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class PgStore:
    def __init__(self, db: DB, table: str = DEFAULT_TABLE, require_tx: bool = False) -> None:
        if not _TABLE_NAME_RE.match(table):
            raise ValueError(f"outboxkit: invalid table name {table!r}")
        self._db = db
        self._table = table
        self._require_tx = require_tx
        self._insert_sql = (
            f"INSERT INTO {table} (subject, payload, created_at) VALUES ($1, $2, NOW())"
        )
        self._list_sql = (
            f"SELECT id, subject, payload, attempts FROM {table} "
            "WHERE published_at IS NULL AND dead_at IS NULL "
            "ORDER BY created_at, id LIMIT $1 FOR UPDATE SKIP LOCKED"
        )
        self._mark_sql = f"UPDATE {table} SET published_at = NOW() WHERE id = ANY($1::bigint[])"
        self._fail_sql = (
            f"UPDATE {table} o SET\n"
            "    attempts = o.attempts + 1,\n"
            "    last_error = f.err,\n"
            "    dead_at = CASE WHEN f.dead THEN NOW() ELSE o.dead_at END\n"
            "FROM unnest($1::bigint[], $2::text[], $3::bool[]) AS f(id, err, dead)\n"
            "WHERE o.id = f.id"
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
        return self._table

    async def insert(self, subject: str, payload: Any) -> None:
        if self._require_tx and not in_tx():
            raise RuntimeError(
                "outboxkit: insert требует активной транзакции (require_tx) — "
                "оберни в pgkit Transactor.with_tx"
            )
        data = json.dumps(payload, separators=(",", ":"))
        await self._db.execute(self._insert_sql, subject, data)

    async def list_unpublished(self, limit: int) -> list[Message]:
        rows = await self._db.fetch(self._list_sql, limit)
        return [
            Message(id=r["id"], subject=r["subject"], payload=r["payload"], attempts=r["attempts"])
            for r in rows
        ]

    async def mark_published(self, ids: list[int]) -> None:
        if not ids:
            return
        await self._db.execute(self._mark_sql, ids)

    async def record_failures(self, failures: list[Failure]) -> None:
        # Инкремент attempts/last_error, dead_at у исчерпавших max_attempts — одним
        # UPDATE через unnest. Мёртвые строки далее не вычитываются list_unpublished.
        if not failures:
            return
        ids = [f.id for f in failures]
        errs = [f.err for f in failures]
        dead = [f.dead for f in failures]
        await self._db.execute(self._fail_sql, ids, errs, dead)

    async def backlog(self) -> list[SubjectBacklog]:
        rows = await self._db.fetch(self._backlog_sql)
        return [
            SubjectBacklog(
                subject=r["subject"], oldest=float(r["lag_seconds"]), pending=int(r["pending"])
            )
            for r in rows
        ]

    async def ensure_partitions(self, months_ahead: int) -> None:
        await ensure_partitions(self._db, self._table, months_ahead)

    async def drop_old_partitions(self, keep_months: int) -> None:
        await drop_old_partitions(self._db, self._table, keep_months)


def new_store(db: DB, require_tx: bool = False) -> PgStore:
    """Store на таблицу DEFAULT_TABLE (single-instance путь, OutboxPgstoreProvider)."""
    return PgStore(db, DEFAULT_TABLE, require_tx)


def new_store_for(db: DB, table: str, require_tx: bool = False) -> PgStore:
    """Store на произвольную таблицу — для нескольких независимых outbox-инстансов
    (таблица на инстанс). Имя валидируется; схему/партиции заводит caller
    (schema_sql/partition_sql)."""
    return PgStore(db, table, require_tx)
