import pytest
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.flagkit import Config, ConfigError, FlagkitProvider, Store


class _AppProvider(Provider):
    scope = Scope.APP

    @provide
    def flags_config(self) -> Config:
        return Config(definitions="new_checkout=true")


class _BrokenProvider(Provider):
    scope = Scope.APP

    @provide
    def flags_config(self) -> Config:
        # model_construct обходит валидатор: невалидный снимок доходит до EnvStore.
        return Config.model_construct(definitions="bad")


async def test_provider_gives_store() -> None:
    container = make_async_container(FlagkitProvider(), _AppProvider())

    flags = await container.get(Store)

    assert flags.bool("new_checkout") is True
    assert flags.names() == ["new_checkout"]

    await container.close()


async def test_provider_propagates_config_error() -> None:
    container = make_async_container(FlagkitProvider(), _BrokenProvider())

    with pytest.raises(ConfigError):
        await container.get(Store)

    await container.close()
