"""pgstore — `auditkit.Logger` поверх `pgkit.DB`: один INSERT в `audit_log`.

Эмитит `audit_log_writes_total{result}` и `audit_log_write_duration_seconds{result}`;
`MIGRATIONS` — источник схемы для `pgkit.MigrationSources`.
"""

from .migrations import MIGRATIONS
from .module import AuditPgstoreProvider
from .store import Store

__all__ = ["MIGRATIONS", "AuditPgstoreProvider", "Store"]
