"""Источники миграций: `MIGRATIONS` для таблицы `outbox` и `table_migrations` для своей таблицы."""

from __future__ import annotations

import atexit
import re
import shutil
import tempfile
from importlib import resources
from pathlib import Path

from pylibinfra.outboxkit.errors import ConfigError
from pylibinfra.pgkit import Source

from .partitions import schema_sql

MIGRATIONS = Source(name="outboxkit", path=str(resources.files(__package__) / "migrations"))
"""Миграции таблицы `outbox` (таблица версий `schema_migrations_outboxkit`)."""

# Имя источника миграций <= 45 символов — ограничение pgkit.Source (schema_migrations_<name>).
_SOURCE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,44}$")

_GENERATED: dict[str, Source] = {}


def table_migrations(table: str) -> Source:
    """Источник миграций outbox-таблицы с произвольным именем (multi-instance).

    Та же схема, своя таблица версий `schema_migrations_<table>`. DDL идемпотентен
    (`IF NOT EXISTS`), поэтому источник применяется и поверх таблицы, заведённой
    вручную: недостающие колонки доедут миграцией. Файлы генерируются во временный
    каталог, который снимается на выходе процесса.
    """
    if not _SOURCE_NAME_RE.match(table):
        raise ConfigError(
            f"outboxkit: invalid migrations table name {table!r}: "
            "up to 45 characters of [a-z0-9_], starting with a letter"
        )
    cached = _GENERATED.get(table)
    if cached is not None:
        return cached

    directory = Path(tempfile.mkdtemp(prefix=f"pylibinfra-outboxkit-{table}-"))
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    (directory / f"0001_{table}.sql").write_text(
        f"{schema_sql(table)}\n\n{partitions_ddl(table)}", encoding="utf-8"
    )
    (directory / f"0001_{table}.rollback.sql").write_text(
        f"DROP TABLE IF EXISTS {table};\n", encoding="utf-8"
    )
    (directory / f"0002_{table}_next_attempt.sql").write_text(
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ;\n",
        encoding="utf-8",
    )
    (directory / f"0002_{table}_next_attempt.rollback.sql").write_text(
        f"ALTER TABLE {table} DROP COLUMN IF EXISTS next_attempt_at;\n", encoding="utf-8"
    )
    source = Source(name=table, path=str(directory))
    _GENERATED[table] = source
    return source


def partitions_ddl(table: str) -> str:
    """Партиции текущего месяца и трёх следующих; дальше их держит `partition_keeper_job`."""
    return f"""DO $$
DECLARE
    base   DATE := DATE_TRUNC('month', NOW())::DATE;
    i      INT;
    pstart DATE;
    pend   DATE;
    pname  TEXT;
BEGIN
    FOR i IN 0..3 LOOP
        pstart := (base + (i || ' month')::INTERVAL)::DATE;
        pend   := (base + ((i + 1) || ' month')::INTERVAL)::DATE;
        pname  := '{table}_' || TO_CHAR(pstart, 'YYYY_MM');
        EXECUTE FORMAT(
            'CREATE TABLE IF NOT EXISTS %I PARTITION OF {table} FOR VALUES FROM (%L) TO (%L)',
            pname, pstart, pend
        );
    END LOOP;
END $$;
"""
