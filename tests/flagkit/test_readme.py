"""Быстрый старт README flagkit — исполняемый."""

import pytest
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.flagkit import Config, EnvStore, FlagkitProvider, Store, new_config


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def flags_config(self) -> Config:
        return new_config()


async def test_readme_quickstart(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_FLAGS", "new_checkout=true,region=eu")

    container = make_async_container(FlagkitProvider(), AppProvider())
    flags = await container.get(Store)

    assert flags.bool("new_checkout") is True
    assert flags.str("region") == "eu"
    assert flags.names() == ["new_checkout", "region"]
    assert flags.bool("unknown") is False
    assert flags.str("unknown") == ""

    await container.close()


async def test_readme_without_di(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_FLAGS", "new_checkout=true,region=eu")

    flags = EnvStore(new_config())

    assert flags.bool("new_checkout") is True


def test_readme_testing_snippet() -> None:
    store = EnvStore(Config(definitions="foo=true,bar=baz"))

    assert store.bool("foo") is True
    assert store.str("bar") == "baz"
    assert store.bool("unknown") is False
