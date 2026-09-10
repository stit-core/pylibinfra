"""bruteforcekit — guard против brute-force на auth-эндпойнты.

Считает неудачные попытки по паре `(subject, ip)` и при достижении порога
блокирует её на `BRUTEFORCE_LOCK_DURATION`. Core (`Guard`, `Cleaner`, `Config`)
отделён от pgstore, который эмитит `bruteforce_checks_total{result}`,
`bruteforce_locks_total` и `bruteforce_cleanup_deleted_total`.
"""

from .config import (
    FULL_IPV6_PREFIX,
    MAX_SUBJECT_LEN,
    Config,
    ConfigError,
    InvalidKeyError,
    new_config,
)
from .guard import Cleaner, Guard, LockState

__all__ = [
    "FULL_IPV6_PREFIX",
    "MAX_SUBJECT_LEN",
    "Cleaner",
    "Config",
    "ConfigError",
    "Guard",
    "InvalidKeyError",
    "LockState",
    "new_config",
]
