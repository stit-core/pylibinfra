"""config — загрузчик env (pydantic-settings) + Vault (hvac), композиция Source."""

from .loader import Source, load
from .module import ConfigProvider, new_vault_client_config
from .vault import VaultClientConfig, VaultSource, new_vault_client

__all__ = [
    "ConfigProvider",
    "Source",
    "VaultClientConfig",
    "VaultSource",
    "load",
    "new_vault_client",
    "new_vault_client_config",
]
