from dishka import Provider, Scope, provide

from pylibinfra.outboxkit.outbox import Store
from pylibinfra.pgkit import DB
from pylibinfra.timerkit import PeriodicJob

from .store import PgStore

# Партиция следующего месяца создаётся заранее, чтобы insert не падал при
# переходе календаря в ещё несозданную партицию. Миграция создаёт текущий месяц
# + 3 вперёд; keeper гоняется раз в сутки, идемпотентен.
_PARTITION_KEEPER_INTERVAL = 24 * 3600.0


# Provider даёт PgStore как outboxkit.Store (его потребляют outboxkit.Bus/Worker
# из OutboxProvider). PgStore реализует и LagReporter — OutboxProvider включает
# outbox_lag_seconds, обнаружив это структурно.
class OutboxPgstoreProvider(Provider):
    scope = Scope.APP

    @provide(provides=Store)
    def store(self, db: DB) -> PgStore:
        return PgStore(db)


def partition_keeper_job(store: PgStore) -> PeriodicJob:
    return PeriodicJob(
        name="outboxkit.partition-keeper",
        interval=_PARTITION_KEEPER_INTERVAL,
        handler=lambda: store.ensure_partitions(1),
    )
