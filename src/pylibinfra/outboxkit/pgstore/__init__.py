"""pgstore — боевой Postgres-store outboxkit.Store поверх pgkit.DB.

SQL зеркалит Go-аналог 1:1. Таблица outbox партиционирована по месяцам
(RANGE created_at): миграция создаёт текущий месяц + 3 вперёд, partition-keeper
держит партиции впереди календаря. PgStore реализует и outboxkit.LagReporter.
"""

from .migrations import migrations_path
from .module import OutboxPgstoreProvider, partition_keeper_job
from .partitions import ensure_partitions, partition_sql, schema_sql
from .store import PgStore

__all__ = [
    "OutboxPgstoreProvider",
    "PgStore",
    "ensure_partitions",
    "migrations_path",
    "partition_keeper_job",
    "partition_sql",
    "schema_sql",
]
