from typing import Any

import hvac
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class VaultClientConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    addr: str = Field(default="", validation_alias="VAULT_ADDR")
    token: str = Field(default="", validation_alias="VAULT_TOKEN")
    namespace: str = Field(default="", validation_alias="VAULT_NAMESPACE")


def new_vault_client(cfg: VaultClientConfig) -> hvac.Client | None:
    if not cfg.addr:
        return None
    return hvac.Client(
        url=cfg.addr,
        token=cfg.token or None,
        namespace=cfg.namespace or None,
    )


class VaultSource:
    """Source поверх Vault KV v2. path = "<mount>/<key>" (как в golibinfra).

    Значения кладутся в dst по env-именам (validation_alias) — только так
    init-kwargs перекрывают env в pydantic-settings. mapping переименовывает
    ключи секрета в эти env-имена (аналог struct-тега ``vault:"jwt_secret"``);
    без mapping ключ секрета берётся как есть.
    """

    def __init__(
        self,
        client: hvac.Client | None,
        path: str,
        *,
        mapping: dict[str, str] | None = None,
    ) -> None:
        self._client = client
        mount, _, key = path.partition("/")
        self._mount = mount
        self._key = key
        self._mapping = mapping or {}

    def apply(self, dst: dict[str, Any]) -> None:
        if self._client is None:
            return
        resp = self._client.secrets.kv.v2.read_secret_version(
            path=self._key,
            mount_point=self._mount,
            raise_on_deleted_version=True,
        )
        data = resp["data"]["data"]
        for key, value in data.items():
            dst[self._mapping.get(key, key)] = value
