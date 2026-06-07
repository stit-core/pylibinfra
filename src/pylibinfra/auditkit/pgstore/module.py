from dishka import Provider, Scope, provide

from pylibinfra.auditkit.audit import Store
from pylibinfra.pgkit import DB

from .store import PgStore


class AuditPgstoreProvider(Provider):
    scope = Scope.APP

    @provide(provides=Store)
    def store(self, db: DB) -> PgStore:
        return PgStore(db)
