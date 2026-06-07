import json
from datetime import UTC, datetime

from pylibinfra.auditkit.audit import Entry
from pylibinfra.pgkit import DB

# asyncpg шлёт str как text — кастуем $5 в jsonb явно (Go marshal'ит metadata в
# JSON-байты, столбец JSONB).
_INSERT_AUDIT_SQL = """
INSERT INTO audit_log (actor_id, action, resource, resource_id, metadata, ip, request_id, at)
VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8)
"""


class PgStore:
    def __init__(self, db: DB) -> None:
        self._db = db

    async def record(self, entry: Entry) -> None:
        at = entry.at if entry.at is not None else datetime.now(UTC)

        meta_json = json.dumps(entry.metadata) if entry.metadata else None

        await self._db.execute(
            _INSERT_AUDIT_SQL,
            _null_str(entry.actor_id),
            entry.action,
            entry.resource,
            _null_str(entry.resource_id),
            meta_json,
            _null_str(entry.ip),
            _null_str(entry.request_id),
            at,
        )


def _null_str(s: str) -> str | None:
    return s or None
