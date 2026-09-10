"""Быстрый старт README config — исполняемый: App-Provider, свой источник, приставка."""

import pytest
from dishka import Provider, Scope, make_async_container, provide
from pydantic import Field

from pylibinfra import config
from pylibinfra._internal.configkit import KitSettings
from pylibinfra.flagkit import Config as FlagConfig
from pylibinfra.flagkit import FlagkitProvider, Store
from pylibinfra.grpckit import Config as GrpcConfig


class VaultSource:
    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def apply(self, dst: dict[str, object]) -> None:
        dst.update(self._secrets)


class AppProvider(Provider):
    scope = Scope.APP

    def __init__(self, secrets: dict[str, str]) -> None:
        super().__init__()
        self._sources = (VaultSource(secrets), config.env_prefixed("myapp"))

    @provide
    def grpc_config(self) -> GrpcConfig:
        return config.load(GrpcConfig, *self._sources)

    @provide
    def flags_config(self) -> FlagConfig:
        return config.load(FlagConfig, *self._sources)


class AppConfig(KitSettings):
    tenant: str = Field(default="", validation_alias="APP_TENANT")


async def test_readme_quickstart(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRPC_ADDR", "127.0.0.1:9090")
    monkeypatch.setenv("MYAPP_FEATURE_FLAGS", "new_checkout=true")

    container = make_async_container(
        FlagkitProvider(),
        AppProvider({"GRPC_SHUTDOWN_GRACE": "3"}),
    )

    grpc_cfg = await container.get(GrpcConfig)
    assert grpc_cfg.addr == "127.0.0.1:9090"  # ENV-слой
    assert grpc_cfg.shutdown_grace == 3.0  # источник перекрыл дефолт
    assert (await container.get(Store)).bool("new_checkout") is True  # приставка

    await container.close()


def test_readme_app_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYAPP_APP_TENANT", "acme")

    assert config.load(AppConfig, config.env_prefixed("myapp")).tenant == "acme"
