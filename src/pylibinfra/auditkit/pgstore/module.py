"""dishka-проводка pgstore: `Store` под портом `auditkit.Logger`."""

from __future__ import annotations

from dishka import Provider, Scope, provide

from pylibinfra.auditkit.audit import Logger
from pylibinfra.pgkit import DB

from .store import Store


class AuditPgstoreProvider(Provider):
    """Отдаёт `Store` как `auditkit.Logger`; `pgkit.DB` регистрирует caller (`PgProvider`).

    Миграции кита (`pgstore.MIGRATIONS`) caller кладёт в `pgkit.MigrationSources`
    сам — таблица `audit_log` иначе не появится.
    """

    scope = Scope.APP

    @provide(provides=Logger)
    def store(self, db: DB) -> Store:
        """Store поверх пула/транзакции pgkit."""
        return Store(db)
