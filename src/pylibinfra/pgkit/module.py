"""dishka-проводка кита: пул/DB/Transactor/ReadWriteDB и прогон миграций на старте."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import NewType

from dishka import Provider, Scope, provide

from .db import DB
from .metrics import ROLE_PRIMARY, ROLE_REPLICA, instrument_pool
from .migrate import (
    Migrated,
    MigrateRunnerConfig,
    MigrationSources,
    migrate_up_all,
    startup_sources,
)
from .pool import PoolConfig, new_pool
from .replica import ReadWriteDB, ReplicaConfig
from .transactor import PoolTransactor, Transactor
from .types import Pool

# Реплика-пул держим отдельным DI-ключом, чтобы не путать с мастер-Pool и дать
# ему собственный lifecycle. None — реплика не сконфигурирована.
ReplicaPool = NewType("ReplicaPool", object)

# dishka проверяет граф целиком на сборке контейнера, поэтому «опционально» здесь
# выражается типом-обёрткой: PgProvider даёт None, а консьюмер с репликой
# регистрирует свой Provider ПОСЛЕ и перекрывает дефолт.
OptionalReplicaConfig = ReplicaConfig | None


class PgProvider(Provider):
    """Граф пула primary: `Pool`, `DB`, `Transactor` и `ReadWriteDB`.

    Config'и (`PoolConfig`, `ReplicaConfig`) регистрирует caller: Provider их не
    provide'ит (asymmetry note). Метрики идут в глобальный Meter — без
    сконфигурированного MeterProvider'а инструменты no-op, граф поднимается.
    """

    scope = Scope.APP

    @provide
    async def pool(self, cfg: PoolConfig) -> AsyncIterator[Pool]:
        """Пул primary под метриками `pg_pool_*{role=primary}`; закрывается на выходе."""
        p = await new_pool(cfg)
        registration = instrument_pool(p, ROLE_PRIMARY)
        try:
            yield p
        finally:
            registration.unregister()
            await p.close()

    @provide
    def db(self, pool: Pool) -> DB:
        """Фасад запросов поверх пула primary."""
        return DB(pool)

    @provide(provides=Transactor)
    def transactor(self, pool: Pool) -> PoolTransactor:
        """Transactor поверх пула primary."""
        return PoolTransactor(pool)

    @provide
    def default_replica_config(self) -> OptionalReplicaConfig:
        """Дефолт «реплики нет»; консьюмер с репликой перекрывает его своим провайдером."""
        return None

    @provide
    async def replica_pool(
        self, cfg: PoolConfig, rep_cfg: OptionalReplicaConfig
    ) -> AsyncIterator[ReplicaPool | None]:
        """Пул реплики под `pg_pool_*{role=replica}`; при пустом `PG_REPLICA_DSN` — None."""
        if rep_cfg is None or not rep_cfg.has_replica():
            yield None
            return
        p = await new_pool(rep_cfg.pool_config(cfg))
        registration = instrument_pool(p, ROLE_REPLICA)
        try:
            yield ReplicaPool(p)
        finally:
            registration.unregister()
            await p.close()

    @provide
    def read_write_db(self, master: DB, replica_pool: ReplicaPool | None) -> ReadWriteDB:
        """Пара read/write; без реплики обе стороны указывают на мастер."""
        if replica_pool is None:
            return ReadWriteDB(master, None)
        return ReadWriteDB(master, DB(replica_pool))


class PgMigrateProvider(Provider):
    """Прогон миграций на старте: источники китов по имени, каталог приложения последним.

    Provide'ит маркер `Migrated`; ресурс, которому нужна применённая схема
    (партиции outbox, cleanup-job), объявляет его зависимостью — тогда порядок
    не зависит от порядка регистрации провайдеров. Consumer резолвит `Migrated`
    из контейнера на старте, чтобы миграции применились до приёма трафика.
    """

    scope = Scope.APP

    @provide
    def default_sources(self) -> MigrationSources:
        """Пустой набор источников китов; consumer перекрывает его своим провайдером."""
        return ()

    @provide
    async def migrated(
        self, cfg: PoolConfig, runner: MigrateRunnerConfig, sources: MigrationSources
    ) -> Migrated:
        """Применяет миграции под advisory-lock'ом в отдельном потоке (yoyo синхронный)."""
        ordered = startup_sources(sources, runner.migrations_path)
        if ordered:
            await asyncio.to_thread(
                migrate_up_all,
                cfg.dsn.get_secret_value(),
                *ordered,
                timeout=runner.migrate_timeout,
            )
        return Migrated()
