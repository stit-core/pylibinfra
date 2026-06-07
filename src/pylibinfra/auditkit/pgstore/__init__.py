"""pgstore — backend auditkit.Store поверх pgkit.DB (INSERT INTO audit_log).

migrations_path() отдаёт каталог .sql-схемы для прогона через pgkit.migrate_up.
"""

from .migrations import migrations_path
from .module import AuditPgstoreProvider
from .store import PgStore

__all__ = [
    "AuditPgstoreProvider",
    "PgStore",
    "migrations_path",
]
