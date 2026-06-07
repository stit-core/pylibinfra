"""Путь к встроенным yoyo-миграциям outbox (аналог embed.FS в Go). Caller
скармливает его pgkit.MigrateConfig.path / migrate_up."""

from importlib import resources


def migrations_path() -> str:
    return str(resources.files(__package__) / "migrations")
