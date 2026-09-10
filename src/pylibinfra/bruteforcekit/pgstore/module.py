"""dishka-проводка pgstore: один `Store` под портами `Guard` и `Cleaner` плюс cleanup-job."""

from __future__ import annotations

from dishka import Provider, Scope, provide

from pylibinfra import timerkit
from pylibinfra.bruteforcekit.config import Config
from pylibinfra.bruteforcekit.guard import Cleaner, Guard
from pylibinfra.pgkit import DB

from .store import Store


def cleanup_job(store: Store, cfg: Config) -> timerkit.Job:
    """Периодическая чистка протухших строк; интервал он же дедлайн одного прогона."""
    return timerkit.Job(
        name="bruteforcekit.cleanup",
        interval=cfg.cleanup_interval,
        run=store.cleanup,
        timeout=cfg.cleanup_interval,
    )


class BruteforcePgstoreProvider(Provider):
    """Отдаёт один `Store` под `Guard` и `Cleaner`; `DB` и `Config` регистрирует caller.

    Миграции кита (`pgstore.MIGRATIONS`) caller кладёт в `pgkit.MigrationSources`,
    а `cleanup_job` — в `timerkit.Jobs`: без job'а таблица не чистится, и её рост
    attacker-driven.
    """

    scope = Scope.APP

    @provide
    def store(self, db: DB, cfg: Config) -> Store:
        """Store поверх пула pgkit; невалидный Config — `bruteforcekit.ConfigError`."""
        return Store(db, cfg)

    @provide(provides=Guard)
    def guard(self, store: Store) -> Store:
        """Тот же Store под request-path портом."""
        return store

    @provide(provides=Cleaner)
    def cleaner(self, store: Store) -> Store:
        """Тот же Store под maintenance-портом."""
        return store
