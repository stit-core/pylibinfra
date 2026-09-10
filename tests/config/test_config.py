from typing import Any

import pytest
from pydantic import Field, SecretStr

from pylibinfra._internal.configkit import KitSettings
from pylibinfra.config import ConfigError, env_prefixed, load


class _Settings(KitSettings):
    secret: str = Field(default="", validation_alias="MY_SECRET")
    other: str = Field(default="def", validation_alias="OTHER")


class _DictSource:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def apply(self, dst: dict[str, Any]) -> None:
        dst.update(self._data)


class _Boom:
    def __init__(self, message: str) -> None:
        self._message = message

    def apply(self, dst: dict[str, Any]) -> None:
        raise RuntimeError(self._message)


def test_env_is_base_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET", "from-env")

    assert load(_Settings).secret == "from-env"


def test_source_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET", "from-env")

    settings = load(_Settings, _DictSource({"MY_SECRET": "from-vault"}))

    assert settings.secret == "from-vault"
    assert settings.other == "def"


def test_later_source_wins() -> None:
    settings = load(
        _Settings,
        _DictSource({"MY_SECRET": "first"}),
        _DictSource({"MY_SECRET": "second"}),
    )

    assert settings.secret == "second"


def test_no_sources_is_env_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_SECRET", raising=False)

    assert load(_Settings).secret == ""


def test_all_source_errors_are_collected() -> None:
    with pytest.raises(ConfigError) as exc_info:
        load(_Settings, _Boom("first down"), _DictSource({}), _Boom("third down"))

    message = str(exc_info.value)
    assert "source[0]: first down" in message
    assert "source[2]: third down" in message


def test_model_is_not_built_when_a_source_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET", "from-env")

    with pytest.raises(ConfigError, match=r"source\[0\]"):
        load(_Settings, _Boom("down"))


def test_validation_error_does_not_leak_values() -> None:
    class _Secrets(KitSettings):
        tok: SecretStr = Field(default=SecretStr(""))
        n: int = Field(default=0)

    with pytest.raises(ConfigError) as exc_info:
        load(_Secrets, _DictSource({"tok": ["super-secret-token"], "n": "not-an-int"}))

    message = str(exc_info.value)
    assert "super-secret-token" not in message
    assert "not-an-int" not in message
    assert "tok" in message
    assert "n" in message


def test_config_error_is_value_error() -> None:
    assert issubclass(ConfigError, ValueError)


def test_env_prefixed_overrides_bare_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET", "bare")
    monkeypatch.setenv("OTHER", "bare-other")
    monkeypatch.setenv("MYAPP_MY_SECRET", "prefixed")

    settings = load(_Settings, env_prefixed("myapp"))

    assert settings.secret == "prefixed"
    # Без префиксованной переменной значение берётся из голого имени (ENV-слой).
    assert settings.other == "bare-other"


@pytest.mark.parametrize("prefix", ["myapp", "MYAPP", " myapp ", "myapp_", "_myapp"])
def test_env_prefixed_normalizes_prefix(prefix: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYAPP_MY_SECRET", "prefixed")

    assert load(_Settings, env_prefixed(prefix)).secret == "prefixed"


@pytest.mark.parametrize("prefix", ["", "  ", "___"])
def test_env_prefixed_rejects_empty_prefix(prefix: str) -> None:
    with pytest.raises(ConfigError, match="empty prefix"):
        env_prefixed(prefix)


def test_env_prefixed_ignores_bare_prefix_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYAPP_", "junk")
    monkeypatch.setenv("MYAPP_MY_SECRET", "prefixed")

    assert load(_Settings, env_prefixed("myapp")).secret == "prefixed"
