"""pgstore — `bruteforcekit.Guard`/`Cleaner` поверх `pgkit.DB` на таблице
`bruteforce_attempts`. Эмитит `bruteforce_checks_total{result}`,
`bruteforce_locks_total`, `bruteforce_cleanup_deleted_total`; `MIGRATIONS` —
источник схемы для `pgkit.MigrationSources`."""

from .migrations import MIGRATIONS
from .module import BruteforcePgstoreProvider, cleanup_job
from .store import CLEANUP_BATCH_SIZE, BruteforceError, Store

__all__ = [
    "CLEANUP_BATCH_SIZE",
    "MIGRATIONS",
    "BruteforceError",
    "BruteforcePgstoreProvider",
    "Store",
    "cleanup_job",
]
