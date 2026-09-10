"""Store — реализация `auditkit.Logger` поверх Postgres: один INSERT в `audit_log`."""

from __future__ import annotations

import json
import time

from pylibinfra.auditkit.audit import AuditError, Entry
from pylibinfra.pgkit import DB

from .metrics import RESULT_ERROR, RESULT_OK, Metrics

# Нулевой at → NOW() базы: одни часы для всех записей, как у DEFAULT колонки.
# $5::jsonb — asyncpg шлёт str как text, столбец JSONB.
_INSERT_SQL = """
INSERT INTO audit_log (actor_id, action, resource, resource_id, metadata, ip, request_id, at)
VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, COALESCE($8, NOW()))
"""


class Store:
    """`auditkit.Logger` на таблице `audit_log`."""

    def __init__(self, db: DB) -> None:
        self._db = db
        self._metrics = Metrics()

    async def log(self, entry: Entry) -> None:
        """Пишет запись в `audit_log`.

        Внутри `pgkit.Transactor.with_tx` INSERT участвует в этой транзакции
        (откат домена откатывает и аудит), вне неё идёт отдельным соединением
        пула в autocommit. Невалидная `Entry` и несериализуемая `metadata` —
        отказ до запроса.
        """
        try:
            entry.validate()
        except Exception:
            self._metrics.invalid()
            raise

        try:
            metadata = json.dumps(entry.metadata) if entry.metadata else None
        except (TypeError, ValueError) as exc:
            self._metrics.invalid()
            raise AuditError(f"auditkit: marshal metadata: {exc}") from exc

        started = time.perf_counter()
        try:
            await self._db.execute(
                _INSERT_SQL,
                _null(entry.actor_id),
                entry.action,
                entry.resource,
                _null(entry.resource_id),
                metadata,
                _null(entry.ip),
                _null(entry.request_id),
                entry.at,
            )
        except Exception as exc:
            self._metrics.observe_write(RESULT_ERROR, time.perf_counter() - started)
            raise AuditError(f"auditkit: insert: {exc}") from exc
        self._metrics.observe_write(RESULT_OK, time.perf_counter() - started)


def _null(value: str) -> str | None:
    return value or None
