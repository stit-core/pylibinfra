"""pgkit — DB-фасад поверх asyncpg: пул, Transactor (contextvars), маппинг
pg-ошибок, migrate-runner (yoyo), health-probe, read-replica, db_pool_* метрики."""

from .db import DB
from .errors import (
    is_check_violation,
    is_foreign_key_violation,
    is_no_rows,
    is_not_null_violation,
    is_retryable,
    is_unique_violation,
)
from .metrics import instrument_pool
from .migrate import MigrateConfig, migrate_up
from .module import PgProvider, new_pool_config, new_replica_config
from .pool import PoolConfig, new_pool
from .probe import DBProbe, db_probe
from .replica import ReadWriteDB, ReplicaConfig
from .transactor import Executor, PoolTransactor, Transactor, conn
from .types import Connection, Pool, Record

__all__ = [
    "DB",
    "Connection",
    "DBProbe",
    "Executor",
    "MigrateConfig",
    "PgProvider",
    "Pool",
    "PoolConfig",
    "PoolTransactor",
    "ReadWriteDB",
    "Record",
    "ReplicaConfig",
    "Transactor",
    "conn",
    "db_probe",
    "instrument_pool",
    "is_check_violation",
    "is_foreign_key_violation",
    "is_no_rows",
    "is_not_null_violation",
    "is_retryable",
    "is_unique_violation",
    "migrate_up",
    "new_pool",
    "new_pool_config",
    "new_replica_config",
]
