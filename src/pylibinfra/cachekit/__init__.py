"""cachekit — доступ к Redis: `Cache` с TTL, резервами и тегами, JSON-хелперы и идемпотентность.

Эмитит `cache_commands_total{cmd,result}`, `cache_command_duration_seconds{cmd}`
и клиентские спаны `redis <cmd>`; проба `cachekit` — `PING`.
"""

from .cache import Cache, RedisCache, get_json, set_json
from .config import Config, new_config
from .errors import CacheDecodeError, CacheError, ConfigError, ServerVersionError
from .idempotency import IdempotencyStore
from .memory import MemoryCache
from .module import CachekitProvider, new_cache, probe

__all__ = [
    "Cache",
    "CacheDecodeError",
    "CacheError",
    "CachekitProvider",
    "Config",
    "ConfigError",
    "IdempotencyStore",
    "MemoryCache",
    "RedisCache",
    "ServerVersionError",
    "get_json",
    "new_cache",
    "new_config",
    "probe",
    "set_json",
]
