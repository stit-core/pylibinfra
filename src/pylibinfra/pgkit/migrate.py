"""Migrate-runner поверх yoyo: независимые источники миграций под advisory-lock'ом.

У каждого источника своя таблица версий `schema_migrations_<name>`, поэтому
новая миграция кита не сдвигает счётчик приложения. Прогон синхронный (yoyo
ходит в базу через psycopg2) — из async-кода зови его через `asyncio.to_thread`.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

import psycopg2  # type: ignore[import-untyped]
from pydantic import Field, model_validator
from yoyo import get_backend, read_migrations  # type: ignore[import-untyped]

from pylibinfra._internal import configkit
from pylibinfra._internal.configkit import KitSettings

from .dsn import safe_error
from .errors import ConfigError, MigrateError

AppSourceName = "app"

# Ключ сессионного advisory-lock'а: FNV-1a 64 от этой строки, приведённый к
# знаковому bigint — тип аргумента pg_advisory_lock.
ADVISORY_LOCK_NAME = "pylibinfra-migrate"

DEFAULT_MIGRATE_TIMEOUT = 120.0

# <= 45 символов, чтобы schema_migrations_<name> уложился в 63 байта идентификатора Postgres.
SOURCE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,44}$")

_VERSION_RE = re.compile(r"^(\d+)")


@dataclass(frozen=True, slots=True)
class Source:
    """Источник миграций с собственной таблицей версий `schema_migrations_<name>`.

    `path` — каталог с yoyo-миграциями (`NNNN_name.sql` + `NNNN_name.rollback.sql`).
    Оператор, которому нужен `CREATE INDEX CONCURRENTLY`, ставит в файле
    директиву `-- transactional: false`.
    """

    name: str
    path: str

    def version_table(self) -> str:
        """Имя таблицы версий источника."""
        return f"schema_migrations_{self.name}"

    def validate(self) -> None:
        """Проверяет имя и наличие `*.sql` в каталоге; нарушение — `pgkit.MigrateError`."""
        if not SOURCE_NAME_RE.match(self.name):
            raise MigrateError(f"pgkit: migrate: invalid source name {self.name!r}")
        directory = Path(self.path)
        if not directory.is_dir():
            raise MigrateError(
                f"pgkit: migrate: source {self.name!r}: {self.path!r} is not a directory"
            )
        if not any(directory.glob("*.sql")):
            raise MigrateError(
                f"pgkit: migrate: source {self.name!r}: no migrations in {self.path!r}"
            )


class Migrated:
    """Маркер «миграции применены»: зависимость от него ставит ресурс после миграций."""


MigrationSources = tuple[Source, ...]


class MigrateRunnerConfig(KitSettings):
    """Параметры прогона миграций на старте приложения (`PgMigrateProvider`)."""

    # env_ignore_empty выключен намеренно: пустой PG_MIGRATIONS_PATH — это
    # «у приложения своих миграций нет», а не «взять дефолт».
    model_config = configkit.settings_config(env_ignore_empty=False)

    # Имя поля не `path`: populate_by_name делает bare-имя поля вторым env-ключом,
    # и `path` подхватил бы системный PATH.
    migrations_path: str = Field(default="/migrations", validation_alias="PG_MIGRATIONS_PATH")
    migrate_timeout: float = Field(
        default=DEFAULT_MIGRATE_TIMEOUT, validation_alias="PG_MIGRATE_TIMEOUT"
    )

    @model_validator(mode="after")
    def _check(self) -> Self:
        """Проверяет инварианты; текст ошибки называет ENV-переменную, а не поле."""
        if self.migrations_path != self.migrations_path.strip():
            raise ValueError(
                f"PG_MIGRATIONS_PATH has surrounding whitespace: {self.migrations_path!r}"
            )
        if self.migrate_timeout <= 0:
            raise ValueError(f"PG_MIGRATE_TIMEOUT must be > 0, got {self.migrate_timeout:g}")
        return self


def new_migrate_runner_config() -> MigrateRunnerConfig:
    """Читает `MigrateRunnerConfig` из ENV; нарушение инварианта — `pgkit.ConfigError`."""
    return configkit.build(MigrateRunnerConfig, "pgkit", ConfigError)


def advisory_lock_key() -> int:
    """FNV-1a 64 от `ADVISORY_LOCK_NAME` как знаковый bigint — ключ `pg_advisory_lock`."""
    h = 0xCBF29CE484222325
    for byte in ADVISORY_LOCK_NAME.encode():
        h ^= byte
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h - (1 << 64) if h >= (1 << 63) else h


def migrate_up(dsn: str, source: Source) -> None:
    """Применяет неприменённые миграции источника; «нечего применять» — не ошибка."""
    with _backend(dsn, source) as backend:
        migrations = read_migrations(source.path)
        _run(dsn, source, lambda: backend.apply_migrations(backend.to_apply(migrations)))


def migrate_down(dsn: str, source: Source) -> None:
    """Откатывает ровно одну последнюю применённую миграцию источника."""
    with _backend(dsn, source) as backend:
        migrations = read_migrations(source.path)
        applied = backend.to_rollback(migrations)
        if not applied:
            return
        _run(dsn, source, lambda: backend.rollback_migrations(applied[:1]))


def migrate_version(dsn: str, source: Source) -> int:
    """Номер (`NNNN`-префикс) последней применённой миграции источника; 0 — ни одной."""
    with _backend(dsn, source) as backend:
        migrations = read_migrations(source.path)
        applied = [m for m in migrations if backend.is_applied(m)]
        if not applied:
            return 0
        return max(_version_of(m.id) for m in applied)


def migrate_up_all(dsn: str, *sources: Source, timeout: float = DEFAULT_MIGRATE_TIMEOUT) -> None:
    """Применяет источники по порядку под одним advisory-lock'ом.

    Из одновременно стартующих инстансов мигрирует ровно один, остальные ждут в
    очереди. `timeout` ограничивает коннект и ожидание lock'а, но не прерывает
    уже идущий SQL-оператор: yoyo не отменяет запрос по дедлайну.
    """
    if not sources:
        return
    seen: set[str] = set()
    for source in sources:
        source.validate()
        if source.name in seen:
            raise MigrateError(f"pgkit: migrate: duplicate source name {source.name!r}")
        seen.add(source.name)

    with _advisory_lock(dsn, timeout):
        for source in sources:
            migrate_up(dsn, source)


def startup_sources(kits: MigrationSources, app_path: str) -> MigrationSources:
    """Источники китов по имени, каталог приложения последним: его схема ссылается на их таблицы."""
    ordered = tuple(sorted(kits, key=lambda s: s.name))
    if not app_path:
        return ordered
    return (*ordered, Source(name=AppSourceName, path=app_path))


def _version_of(migration_id: str) -> int:
    match = _VERSION_RE.match(migration_id)
    return int(match.group(1)) if match else 0


@contextlib.contextmanager
def _backend(dsn: str, source: Source) -> Iterator[Any]:
    source.validate()
    try:
        backend = get_backend(dsn, migration_table=source.version_table())
    except Exception as exc:
        raise MigrateError(
            f"pgkit: migrate source {source.name!r}: connect: {safe_error(dsn, exc)}"
        ) from exc
    # Служебные таблицы yoyo — тоже на источник: их версию yoyo определяет по
    # наличию таблицы миграций, и для второго источника общий _yoyo_log
    # пересоздавался бы поверх существующего.
    backend.log_table = f"_yoyo_log_{source.name}"
    backend.version_table = f"_yoyo_version_{source.name}"
    try:
        yield backend
    finally:
        with contextlib.suppress(Exception):
            backend.connection.close()


def _run(dsn: str, source: Source, fn: Callable[[], None]) -> None:
    try:
        fn()
    except Exception as exc:
        raise MigrateError(
            f"pgkit: migrate source {source.name!r}: {safe_error(dsn, exc)}"
        ) from exc


@contextlib.contextmanager
def _advisory_lock(dsn: str, timeout: float) -> Iterator[None]:
    """Держит сессионный advisory-lock; ожидание ограничено `statement_timeout`."""
    key = advisory_lock_key()
    wait_ms = max(int(timeout * 1000), 1)
    try:
        conn = psycopg2.connect(dsn, connect_timeout=max(int(timeout), 1))
    except psycopg2.Error as exc:
        raise MigrateError(f"pgkit: migrate lock: connect: {safe_error(dsn, exc)}") from exc
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            # pg_advisory_lock ждёт бесконечно — единственный выход из очереди
            # для инстанса, который не дождался, это отмена по таймауту.
            cur.execute("SET statement_timeout = %s", (wait_ms,))
            try:
                cur.execute("SELECT pg_advisory_lock(%s)", (key,))
            except psycopg2.errors.QueryCanceled as exc:
                raise MigrateError(
                    f"pgkit: migrate lock: timed out after {timeout:g}s waiting for advisory lock"
                ) from exc
            except psycopg2.Error as exc:
                raise MigrateError(f"pgkit: migrate lock: acquire: {safe_error(dsn, exc)}") from exc
            cur.execute("SET statement_timeout = 0")
        yield
    finally:
        with contextlib.suppress(Exception), conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (key,))
        with contextlib.suppress(Exception):
            conn.close()
