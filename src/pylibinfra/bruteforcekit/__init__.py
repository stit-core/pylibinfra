"""bruteforcekit — guard против brute-force на login-эндпойнты.

Считает неудачные попытки per-subject (логин/email) и per-IP, при превышении
порога (Config.max_failures в окне Config.window) блокирует на
Config.lock_duration и возвращает LockState. Core (Guard + Store-port) отделён
от персистентной реализации: боевой PgStore — в подпакете bruteforcekit.pgstore.
Lockout-истечение вычисляется через clockkit.Clock (тестируемо).
"""

from .config import Config
from .guard import Guard, LockState, Store
from .module import BruteforceProvider, new_config

__all__ = [
    "BruteforceProvider",
    "Config",
    "Guard",
    "LockState",
    "Store",
    "new_config",
]
