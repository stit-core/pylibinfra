"""MigrateConfig + migrate_up — синхронный runner поверх yoyo-migrations.

yoyo блокирующий (psycopg2), поэтому migrate_up — sync-функция; вызывается до
старта event-loop или в executor'е.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from yoyo import get_backend, read_migrations  # type: ignore[import-untyped]


class MigrateConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    dsn: str = Field(validation_alias="PG_DSN")
    path: str = Field(default="/migrations", validation_alias="PG_MIGRATIONS_PATH")


def migrate_up(cfg: MigrateConfig) -> None:
    backend = get_backend(cfg.dsn)
    migrations = read_migrations(cfg.path)
    with backend.lock():
        backend.apply_migrations(backend.to_apply(migrations))
