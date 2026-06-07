"""PgStore — реализация outboxkit.Store поверх pgkit.DB. SQL зеркалит
golibinfra/outboxkit/pgstore/store.go 1:1 (asyncpg-плейсхолдеры $1)."""

import json
from typing import Any

from pylibinfra.outboxkit.outbox import Message
from pylibinfra.pgkit import DB

from .partitions import ensure_partitions

_INSERT_SQL = "INSERT INTO outbox (subject, payload, created_at) VALUES ($1, $2, NOW())"

_LIST_UNPUBLISHED_SQL = (
    "SELECT id, subject, payload FROM outbox WHERE published_at IS NULL "
    "ORDER BY created_at, id LIMIT $1 FOR UPDATE SKIP LOCKED"
)

_MARK_PUBLISHED_SQL = "UPDATE outbox SET published_at = NOW() WHERE id = $1"

_LAG_BY_SUBJECT_SQL = (
    "SELECT subject, EXTRACT(EPOCH FROM (NOW() - MIN(created_at)))::float8 AS lag_seconds "
    "FROM outbox WHERE published_at IS NULL GROUP BY subject"
)


class PgStore:
    def __init__(self, db: DB) -> None:
        self._db = db

    async def insert(self, subject: str, payload: Any) -> None:
        data = json.dumps(payload, separators=(",", ":"))
        await self._db.execute(_INSERT_SQL, subject, data)

    async def list_unpublished(self, limit: int) -> list[Message]:
        rows = await self._db.fetch(_LIST_UNPUBLISHED_SQL, limit)
        return [
            Message(id=r["id"], subject=r["subject"], payload=_decode(r["payload"])) for r in rows
        ]

    async def mark_published(self, id: int) -> None:
        await self._db.execute(_MARK_PUBLISHED_SQL, id)

    async def lag_by_subject(self) -> dict[str, float]:
        rows = await self._db.fetch(_LAG_BY_SUBJECT_SQL)
        return {r["subject"]: float(r["lag_seconds"]) for r in rows}

    async def ensure_partitions(self, months_ahead: int) -> None:
        await ensure_partitions(self._db, months_ahead)


def _decode(payload: Any) -> Any:
    # asyncpg отдаёт jsonb как str (без codec'а); декодим, чтобы re-publish
    # отдавал исходное значение, а не строку с JSON внутри.
    if isinstance(payload, str | bytes | bytearray):
        return json.loads(payload)
    return payload
