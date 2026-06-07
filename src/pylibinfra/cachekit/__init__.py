"""cachekit — Redis-кэш: порт Cache, generic get_json[T]/set_json[T],
tag-invalidation, IdempotencyStore для httpkit, dishka-Provider с redis
lifecycle и OTel-spans.
"""

from .cache import Cache, RedisCache, get_json, set_json
from .config import Config
from .idempotency import CacheIdempotencyStore, IdempotencyStore
from .module import CachekitProvider, new_config, probe

__all__ = [
    "Cache",
    "CacheIdempotencyStore",
    "CachekitProvider",
    "Config",
    "IdempotencyStore",
    "RedisCache",
    "get_json",
    "new_config",
    "probe",
    "set_json",
]
