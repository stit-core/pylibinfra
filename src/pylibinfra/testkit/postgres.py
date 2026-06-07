"""start_postgres — async context-manager: поднимает PostgresContainer, собирает
asyncpg-DSN, опц. применяет миграции через pgkit.migrate_up и отдаёт fixture с
хелпером db() → pgkit.DB."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass

import asyncpg  # type: ignore[import-untyped]
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from pylibinfra.pgkit import DB, MigrateConfig, migrate_up

_USER = "test"
_PASSWORD = "test"  # noqa: S105
_DB = "test"
_PORT = 5432


@dataclass(frozen=True, slots=True)
class PostgresFixture:
    dsn: str
    container: PostgresContainer

    @contextlib.asynccontextmanager
    async def db(self) -> AsyncIterator[DB]:
        """Открывает asyncpg-пул на DSN и отдаёт DB-фасад; пул закрывается на выходе."""
        pool = await asyncpg.create_pool(dsn=self.dsn)
        try:
            yield DB(pool)
        finally:
            await pool.close()


@contextlib.asynccontextmanager
async def start_postgres(migrations_path: str | None = None) -> AsyncIterator[PostgresFixture]:
    container = PostgresContainer(
        "postgres:17-alpine",
        username=_USER,
        password=_PASSWORD,
        dbname=_DB,
        port=_PORT,
    )
    container.start()
    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(_PORT)
        dsn = f"postgresql://{_USER}:{_PASSWORD}@{host}:{port}/{_DB}"

        # yoyo блокирующий (psycopg2), но DSN общий — asyncpg тоже принимает
        # postgresql://; migrate_up sync, прогоняется до отдачи fixture.
        # MigrateConfig — BaseSettings: поля заполняются по env-alias'ам.
        if migrations_path is not None:
            cfg = MigrateConfig.model_validate(
                {"PG_DSN": dsn, "PG_MIGRATIONS_PATH": migrations_path}
            )
            migrate_up(cfg)

        yield PostgresFixture(dsn=dsn, container=container)
    finally:
        container.stop()
