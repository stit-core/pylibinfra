"""Путь к каталогу .sql-миграций audit_log для pgkit.migrate_up.

Аналог Go MigrationsFS: консьюмер прогоняет их через pgkit migrate-runner,
передав migrations_path() в MigrateConfig.path (yoyo читает каталог .sql).
"""

from importlib.resources import files
from pathlib import Path


def migrations_path() -> str:
    return str(Path(str(files(__package__))) / "migrations")
