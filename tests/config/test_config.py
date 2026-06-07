from typing import Any

import pytest
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from pylibinfra.config import VaultClientConfig, VaultSource, load, new_vault_client


class _Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    secret: str = Field(default="", validation_alias="MY_SECRET")
    other: str = Field(default="def", validation_alias="OTHER")


class _DictSource:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def apply(self, dst: dict[str, Any]) -> None:
        dst.update(self._data)


def test_env_is_base_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET", "from-env")
    settings = load(_Settings)
    assert settings.secret == "from-env"


def test_source_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET", "from-env")
    settings = load(_Settings, _DictSource({"MY_SECRET": "from-vault"}))
    assert settings.secret == "from-vault"
    assert settings.other == "def"


def test_source_error_wrapped() -> None:
    class _Boom:
        def apply(self, dst: dict[str, Any]) -> None:
            raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError, match=r"config: source\[0\]"):
        load(_Settings, _Boom())


def test_validation_error_does_not_leak_secret() -> None:
    class _Secrets(BaseSettings):
        model_config = SettingsConfigDict(extra="ignore")

        tok: SecretStr = Field(default=SecretStr(""))
        n: int = Field(default=0)

    class _Bad:
        def apply(self, dst: dict[str, Any]) -> None:
            # невалидный тип для секретного поля → input_value утёк бы в трейсбек
            dst.update({"tok": ["super-secret-token"], "n": "not-an-int"})

    with pytest.raises(RuntimeError) as exc_info:
        load(_Secrets, _Bad())
    msg = str(exc_info.value)
    assert "super-secret-token" not in msg
    assert "not-an-int" not in msg
    assert "tok" in msg
    assert "n" in msg


def test_new_vault_client_none_when_no_addr() -> None:
    assert new_vault_client(VaultClientConfig()) is None


class _FakeKVv2:
    def read_secret_version(
        self, *, path: str, mount_point: str, raise_on_deleted_version: bool
    ) -> dict[str, Any]:
        assert mount_point == "secret"
        assert path == "myapp"
        assert raise_on_deleted_version is True
        return {"data": {"data": {"jwt_secret": "xyz"}}}


class _FakeKV:
    v2 = _FakeKVv2()


class _FakeSecrets:
    kv = _FakeKV()


class _FakeClient:
    secrets = _FakeSecrets()


def test_vault_source_maps_to_env_name() -> None:
    client: Any = _FakeClient()
    dst: dict[str, Any] = {}
    VaultSource(client, "secret/myapp", mapping={"jwt_secret": "JWT_SECRET"}).apply(dst)
    assert dst == {"JWT_SECRET": "xyz"}


def test_vault_source_noop_without_client() -> None:
    dst: dict[str, Any] = {}
    VaultSource(None, "secret/myapp").apply(dst)
    assert dst == {}
