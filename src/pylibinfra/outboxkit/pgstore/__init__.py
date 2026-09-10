"""pgstore — Postgres-реализация `outboxkit.Repository`: партиционированная по месяцам таблица,
тик воркера под `FOR UPDATE`, backoff в колонке, backlog и retention через DETACH+DROP.

Конфиг — `OUTBOX_RETENTION_MONTHS`, `OUTBOX_REQUIRE_TX`. Требует Postgres 14+.
"""

from .migrations import MIGRATIONS, partitions_ddl, table_migrations
from .module import (
    Config,
    OutboxPgstoreFactoryProvider,
    OutboxPgstoreProvider,
    new_config,
    new_repository_factory,
    new_store,
)
from .partitions import (
    PendingRowsError,
    drop_old_partitions,
    ensure_partitions,
    partition_month,
    partition_sql,
    schema_sql,
)
from .store import (
    DEFAULT_TABLE,
    KEEPER_INTERVAL,
    Store,
    partition_keeper_job,
    retention_keeper_job,
)

__all__ = [
    "DEFAULT_TABLE",
    "KEEPER_INTERVAL",
    "MIGRATIONS",
    "Config",
    "OutboxPgstoreFactoryProvider",
    "OutboxPgstoreProvider",
    "PendingRowsError",
    "Store",
    "drop_old_partitions",
    "ensure_partitions",
    "new_config",
    "new_repository_factory",
    "new_store",
    "partition_keeper_job",
    "partition_month",
    "partition_sql",
    "partitions_ddl",
    "retention_keeper_job",
    "schema_sql",
    "table_migrations",
]
