"""Доступ к бандленным .sql-миграциям (аналог embed.FS в Go).

yoyo читает миграции с файловой системы, поэтому отдаём путь к директории —
консьюмер передаёт его в pgkit.migrate_up через MigrateConfig.path.
"""

from importlib.resources import files
from pathlib import Path


def migrations_dir() -> str:
    return str(Path(str(files(__package__).joinpath("migrations"))))
