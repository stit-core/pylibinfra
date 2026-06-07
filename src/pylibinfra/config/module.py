import hvac
from dishka import Provider, Scope, provide

from .vault import VaultClientConfig, new_vault_client


# Provider не provide'ит VaultClientConfig — caller регистрирует сам (asymmetry
# note). vault_client может быть None (VAULT_ADDR пуст) — потребитель проверяет.
class ConfigProvider(Provider):
    scope = Scope.APP

    @provide
    def vault_client(self, cfg: VaultClientConfig) -> hvac.Client | None:
        return new_vault_client(cfg)


def new_vault_client_config() -> VaultClientConfig:
    return VaultClientConfig()
