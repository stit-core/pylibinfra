"""start_postgres — контейнер Postgres для интеграционных тестов и хелперы поверх pgkit."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from pylibinfra.pgkit import (
    DB,
    AppSourceName,
    Pool,
    PoolConfig,
    PoolTransactor,
    Source,
    Transactor,
    migrate_up_all,
    new_pool,
)

from ._docker import POSTGRES_IMAGE, quiet_testcontainers, require_docker

_USER = "test"
_PASSWORD = "test"  # noqa: S105
_DB = "test"
_PORT = 5432
_MIGRATE_TIMEOUT = 60.0


@dataclass(slots=True)
class PostgresFixture:
    """Живой Postgres теста: `dsn`, handle контейнера и общий пул поверх pgkit."""

    dsn: str
    container: PostgresContainer
    _pool: Pool | None = field(default=None, repr=False)

    def pool_config(self) -> PoolConfig:
        """Конфиг пула фикстуры: prod-таймауты pgkit поверх DSN контейнера."""
        # model_validate, а не PoolConfig(...): поля заполняются по именам, а
        # значения фикстуры не подмешиваются из ENV прогона.
        return PoolConfig.model_validate(
            {
                "dsn": self.dsn,
                "max_conns": 5,
                "min_conns": 1,
                "max_conn_idle_time": 300.0,
                "connect_timeout": 10.0,
                "otel_trace": True,
                "statement_timeout": 30.0,
                "idle_in_tx_session_timeout": 60.0,
                "lock_timeout": 5.0,
            }
        )

    async def pool(self) -> Pool:
        """Общий пул через `pgkit.new_pool`: открывается один раз, гасится на выходе CM."""
        if self._pool is None:
            self._pool = await new_pool(self.pool_config())
        return self._pool

    async def db(self) -> DB:
        """`pgkit.DB` поверх общего пула."""
        return DB(await self.pool())

    async def transactor(self) -> Transactor:
        """`pgkit.Transactor` поверх того же пула, что и `db()`."""
        return PoolTransactor(await self.pool())

    async def aclose(self) -> None:
        """Закрывает общий пул, если он открывался."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


@contextlib.asynccontextmanager
async def start_postgres(
    *,
    migrations: Sequence[Source] = (),
    migrations_dir: str | None = None,
    image: str = POSTGRES_IMAGE,
) -> AsyncIterator[PostgresFixture]:
    """Поднимает Postgres (`image`), применяет источники миграций, отдаёт `PostgresFixture`.

    `migrations_dir` добавляется последним источником `pgkit.AppSourceName` — так
    же, как это делает `PgMigrateProvider`. Контейнер и пул гасятся на выходе.
    """
    require_docker()
    quiet_testcontainers()
    container = PostgresContainer(image, username=_USER, password=_PASSWORD, dbname=_DB, port=_PORT)
    fixture: PostgresFixture | None = None
    try:
        # start() внутри try: сорвавшееся ожидание готовности оставляет контейнер
        # запущенным, и снять его может только finally.
        container.start()
        host = container.get_container_host_ip()
        port = container.get_exposed_port(_PORT)
        dsn = f"postgresql://{_USER}:{_PASSWORD}@{host}:{port}/{_DB}"

        sources = list(migrations)
        if migrations_dir is not None:
            sources.append(Source(name=AppSourceName, path=migrations_dir))
        if sources:
            await asyncio.to_thread(migrate_up_all, dsn, *sources, timeout=_MIGRATE_TIMEOUT)

        fixture = PostgresFixture(dsn=dsn, container=container)
        yield fixture
    finally:
        if fixture is not None:
            await fixture.aclose()
        with contextlib.suppress(Exception):
            container.stop()
