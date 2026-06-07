from collections.abc import AsyncIterator
from typing import NewType

from dishka import Provider, Scope, provide
from opentelemetry import metrics

from .db import DB
from .metrics import instrument_pool
from .pool import PoolConfig, new_pool
from .replica import ReadWriteDB, ReplicaConfig
from .transactor import PoolTransactor, Transactor
from .types import Pool

# Реплика-пул держим отдельным DI-ключом, чтобы не путать с мастер-Pool и дать
# ему собственный lifecycle. None — реплика не сконфигурирована.
ReplicaPool = NewType("ReplicaPool", object)


# Provider не provide'ит свои Config'и (asymmetry note) — caller регистрирует их
# сам. Дешёвый mode: new_pool_config()/new_replica_config() ниже. Meter берётся
# из глобального OTel-provider'а (его выставляет telemetry.init) — без telemetry
# в DI инструментация — no-op.
class PgProvider(Provider):
    scope = Scope.APP

    @provide
    async def pool(self, cfg: PoolConfig) -> AsyncIterator[Pool]:
        p = await new_pool(cfg)
        instrument_pool(p, metrics.get_meter("app"))
        yield p
        await p.close()

    @provide
    def db(self, pool: Pool) -> DB:
        return DB(pool)

    @provide(provides=Transactor)
    def transactor(self, pool: Pool) -> PoolTransactor:
        return PoolTransactor(pool)

    @provide
    async def replica_pool(
        self, cfg: PoolConfig, rep_cfg: ReplicaConfig
    ) -> AsyncIterator[ReplicaPool | None]:
        if not rep_cfg.replica_dsn:
            yield None
            return
        rep_pool_cfg = cfg.model_copy(
            update={"dsn": rep_cfg.replica_dsn, "max_conns": rep_cfg.max_conns}
        )
        p = await new_pool(rep_pool_cfg)
        yield ReplicaPool(p)
        await p.close()

    @provide
    def read_write_db(self, master: DB, replica_pool: ReplicaPool | None) -> ReadWriteDB:
        if replica_pool is None:
            return ReadWriteDB(master, None)
        return ReadWriteDB(master, DB(replica_pool))


def new_pool_config() -> PoolConfig:
    return PoolConfig()


def new_replica_config() -> ReplicaConfig:
    return ReplicaConfig()
