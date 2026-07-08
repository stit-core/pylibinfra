"""pgstore — боевой Postgres-store outboxkit.Repository поверх pgkit.DB.

SQL зеркалит Go-аналог 1:1. Таблица outbox партиционирована по месяцам (RANGE
created_at): миграция 0001 создаёт текущий месяц + 3 вперёд, 0002 добавляет
poison-колонки (attempts/last_error/dead_at) + partial-индекс с `dead_at IS NULL`.
partition-keeper держит партиции впереди календаря, retention-keeper дропает
старые (OUTBOX_RETENTION_MONTHS). PgStore реализует и outboxkit.BacklogReporter.
"""

from .migrations import migrations_path
from .module import (
    Config,
    OutboxPgstoreProvider,
    new_config,
    partition_keeper_job,
    retention_keeper_job,
)
from .partitions import (
    drop_old_partitions,
    ensure_partitions,
    partition_sql,
    schema_sql,
)
from .store import DEFAULT_TABLE, PgStore, new_store, new_store_for

__all__ = [
    "DEFAULT_TABLE",
    "Config",
    "OutboxPgstoreProvider",
    "PgStore",
    "drop_old_partitions",
    "ensure_partitions",
    "migrations_path",
    "new_config",
    "new_store",
    "new_store_for",
    "partition_keeper_job",
    "partition_sql",
    "retention_keeper_job",
    "schema_sql",
]
