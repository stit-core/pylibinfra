from dishka import Provider, Scope, provide
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from pylibinfra.outboxkit.outbox import Repository
from pylibinfra.pgkit import DB
from pylibinfra.timerkit import PeriodicJob

from .store import PgStore

# Партиция следующего месяца создаётся заранее, чтобы insert не падал при переходе
# календаря в ещё несозданную партицию. Retention-keeper дропает старые партиции
# раз в сутки. Оба идемпотентны.
_KEEPER_INTERVAL = 24 * 3600.0


class Config(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    # >0 — дропать outbox-партиции старше N месяцев; 0 — не дропать.
    retention_months: int = Field(default=0, validation_alias="OUTBOX_RETENTION_MONTHS")
    # true — insert вне pgkit Transactor.with_tx падает (страхует атомарность).
    require_tx: bool = Field(default=False, validation_alias="OUTBOX_REQUIRE_TX")


# Provider даёт PgStore как outboxkit.Repository (его потребляют outboxkit.Bus/Worker
# из OutboxProvider). PgStore реализует и BacklogReporter — OutboxProvider включает
# outbox_lag_seconds/outbox_pending_count, обнаружив это структурно. Config не
# provide'ится (asymmetry) — caller регистрирует сам (дешёвый mode: new_config()).
class OutboxPgstoreProvider(Provider):
    scope = Scope.APP

    @provide(provides=Repository)
    def store(self, db: DB, cfg: Config) -> PgStore:
        return PgStore(db, require_tx=cfg.require_tx)


def new_config() -> Config:
    return Config()


def partition_keeper_job(store: PgStore) -> PeriodicJob:
    return PeriodicJob(
        name=f"outboxkit.partition-keeper.{store.table}",
        interval=_KEEPER_INTERVAL,
        handler=lambda: store.ensure_partitions(1),
    )


def retention_keeper_job(store: PgStore, keep_months: int) -> PeriodicJob:
    return PeriodicJob(
        name=f"outboxkit.retention.{store.table}",
        interval=_KEEPER_INTERVAL,
        handler=lambda: store.drop_old_partitions(keep_months),
    )
