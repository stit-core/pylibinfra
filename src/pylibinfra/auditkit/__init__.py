"""auditkit — append-only audit-log: core (`Entry`, `Logger`) отделён от
pgstore-реализации. Фиксирует кто/когда/что сделал над каким ресурсом; pgstore
эмитит `audit_log_writes_total{result}` и `audit_log_write_duration_seconds{result}`."""

from .audit import (
    MAX_ACTION_LEN,
    MAX_ACTOR_ID_LEN,
    MAX_RESOURCE_ID_LEN,
    MAX_RESOURCE_LEN,
    AuditError,
    Entry,
    InvalidEntryError,
    Logger,
)

__all__ = [
    "MAX_ACTION_LEN",
    "MAX_ACTOR_ID_LEN",
    "MAX_RESOURCE_ID_LEN",
    "MAX_RESOURCE_LEN",
    "AuditError",
    "Entry",
    "InvalidEntryError",
    "Logger",
]
