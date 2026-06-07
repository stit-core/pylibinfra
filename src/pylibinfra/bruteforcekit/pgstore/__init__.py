"""pgstore — боевая реализация bruteforcekit.Store поверх pgkit.DB.

Счётчики попыток в таблице bruteforce_attempts (миграция бандлится, путь —
migrations_dir(), применяется через pgkit.migrate_up). locked_until считается в
SQL (NOW() + lock_duration), истечение блокировки проверяет Guard через Clock.
"""

from .migrations import migrations_dir
from .module import BruteforcePgstoreProvider
from .store import PgStore

__all__ = ["BruteforcePgstoreProvider", "PgStore", "migrations_dir"]
