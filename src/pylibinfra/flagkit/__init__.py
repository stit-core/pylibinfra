"""flagkit — feature flags из одной переменной окружения `FEATURE_FLAGS`.

Снимок на старте: `Store` отдаёт `bool`/`str`/`names`, невалидная запись роняет
конструирование (`ConfigError`). Кит ничего не эмитит.
"""

from .flag import Config, ConfigError, EnvStore, Store, new_config
from .module import FlagkitProvider

__all__ = [
    "Config",
    "ConfigError",
    "EnvStore",
    "FlagkitProvider",
    "Store",
    "new_config",
]
