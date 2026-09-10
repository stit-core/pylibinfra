"""Конфиг pgstore и dishka-проводка: `Store` как `Repository` плюс фабрика для multi-instance."""

from __future__ import annotations

from typing import Annotated

from dishka import DEFAULT_COMPONENT, FromComponent, Provider, Scope, provide
from pydantic import Field

from pylibinfra._internal import configkit
from pylibinfra._internal.configkit import KitSettings
from pylibinfra.clockkit import Clock
from pylibinfra.outboxkit.errors import ConfigError
from pylibinfra.outboxkit.outbox import Repository, RepositoryFactory
from pylibinfra.pgkit import DB, Migrated

from .store import DEFAULT_TABLE, Store

_KIT = "outboxkit"
_Default = FromComponent(DEFAULT_COMPONENT)


class Config(KitSettings):
    """Env-контракт Postgres-store'а: retention партиций и tx-strict guard на insert."""

    retention_months: int = Field(default=0, ge=0, validation_alias="OUTBOX_RETENTION_MONTHS")
    require_tx: bool = Field(default=True, validation_alias="OUTBOX_REQUIRE_TX")


def new_config() -> Config:
    """Читает `OUTBOX_RETENTION_MONTHS`/`OUTBOX_REQUIRE_TX`; невалидные значения — `ConfigError`."""
    return configkit.build(Config, _KIT, ConfigError)


def new_store(db: DB, cfg: Config, table: str = DEFAULT_TABLE, clock: Clock | None = None) -> Store:
    """Собирает `Store` на таблицу `table` по конфигу pgstore."""
    return Store(
        db,
        table,
        require_tx=cfg.require_tx,
        retention_months=cfg.retention_months,
        clock=clock,
    )


def new_repository_factory(db: DB, cfg: Config, clock: Clock | None = None) -> RepositoryFactory:
    """Фабрика store'ов на произвольные таблицы для `OutboxInstanceProvider`.

    Партиции таблицы создаются при сборке инстанса: первый insert не должен упасть
    в ещё несозданную партицию. Схему таблицы накатывает `table_migrations(table)`.
    """

    async def factory(table: str) -> Repository:
        store = new_store(db, cfg, table, clock)
        await store.ensure_partitions(1)
        return store

    return factory


class OutboxPgstoreProvider(Provider):
    """Single-instance путь: `Store` на таблице `outbox` как `outboxkit.Repository`.

    Зависимость от `pgkit.Migrated` ставит создание партиций после миграций —
    иначе на чистой БД партиции создавались бы поверх отсутствующей таблицы.
    `Config` регистрирует консьюмер (asymmetry note): дешёвый путь — `new_config()`.
    """

    scope = Scope.APP

    def __init__(self, table: str = DEFAULT_TABLE, *, component: str | None = None) -> None:
        super().__init__(component=component)
        self.table = table

    @provide
    async def store(
        self,
        db: Annotated[DB, _Default],
        cfg: Config,
        migrated: Annotated[Migrated, _Default],
    ) -> Store:
        """Store с созданными партициями текущего и следующего месяца."""
        store = new_store(db, cfg, self.table)
        await store.ensure_partitions(1)
        return store

    @provide
    def repository(self, store: Store) -> Repository:
        """Тот же store под core-портом кита."""
        return store


class OutboxPgstoreFactoryProvider(Provider):
    """Multi-instance путь: `RepositoryFactory` для `OutboxInstanceProvider`.

    С `OutboxPgstoreProvider` не сочетается по смыслу: тот держит таблицу `outbox`,
    эта фабрика — таблицы инстансов.
    """

    scope = Scope.APP

    @provide
    def factory(
        self,
        db: Annotated[DB, _Default],
        cfg: Config,
        migrated: Annotated[Migrated, _Default],
    ) -> RepositoryFactory:
        """Фабрика store'ов; партиции таблицы создаются при сборке инстанса."""
        return new_repository_factory(db, cfg)
