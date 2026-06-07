from dishka import Provider, Scope, provide

from pylibinfra.bruteforcekit.config import Config
from pylibinfra.bruteforcekit.guard import Store
from pylibinfra.pgkit import DB

from .store import PgStore


# Provider не provide'ит Config (asymmetry note) — caller регистрирует Config и DB
# сам; здесь только связка Store-port → PgStore поверх pgkit.DB.
class BruteforcePgstoreProvider(Provider):
    scope = Scope.APP

    @provide(provides=Store)
    def store(self, db: DB, cfg: Config) -> PgStore:
        return PgStore(db, cfg)
