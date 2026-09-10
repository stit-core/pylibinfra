import pytest
from pydantic import AliasChoices, BaseModel, Field, SecretStr, model_validator

from pylibinfra._internal import configkit


class KitConfigError(configkit.ConfigError):
    pass


class Cfg(configkit.KitSettings):
    dsn: SecretStr = Field(validation_alias="X_DSN")
    size: int = Field(default=3, validation_alias="X_SIZE")
    path: str = Field(default="/migrations", validation_alias="X_PATH")

    @model_validator(mode="after")
    def _check(self) -> "Cfg":
        if self.size <= 0:
            raise ValueError("size must be > 0")
        return self


class KeepEmpty(configkit.KitSettings):
    model_config = configkit.settings_config(env_ignore_empty=False)

    path: str = Field(default="/migrations", validation_alias="X_PATH")


class Nested(BaseModel):
    a: int = 1


class NoAlias(configkit.KitSettings):
    secret: str = ""
    header: str = "X-Shared-Secret"
    nested: Nested = Field(default_factory=Nested)


class Prefixed(configkit.KitSettings):
    model_config = configkit.settings_config(env_prefix="PG_")

    max_conns: int = Field(default=1, validation_alias="PG_MAX_CONNS")
    plain: int = 0


class SelfAliased(configkit.KitSettings):
    same: str = Field(default="", validation_alias="same")


class Choices(configkit.KitSettings):
    value: str = Field(default="", validation_alias=AliasChoices("A_ONE", "A_TWO"))


class Complex(configkit.KitSettings):
    items: list[str] = Field(default_factory=list, validation_alias="X_ITEMS")


class AliasOnly(configkit.KitSettings):
    model_config = configkit.settings_config(populate_by_name=False)

    size: int = Field(default=3, validation_alias="X_SIZE")
    plain: int = 0


def test_build_reads_env_and_freezes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_DSN", "postgres://secret")
    cfg = configkit.build(Cfg, "x", KitConfigError)
    assert cfg.dsn.get_secret_value() == "postgres://secret"
    with pytest.raises(Exception):  # noqa: B017, PT011 — frozen: pydantic ValidationError
        cfg.size = 5  # type: ignore[misc]


def test_empty_env_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_DSN", "postgres://s")
    monkeypatch.setenv("X_SIZE", "")
    assert configkit.build(Cfg, "x", KitConfigError).size == 3


def test_bare_field_name_is_not_an_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # PATH из окружения не должен становиться полем path; DSN/SIZE — тоже мимо.
    monkeypatch.setenv("X_DSN", "postgres://s")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("DSN", "leak")
    monkeypatch.setenv("SIZE", "99")
    monkeypatch.setenv("size", "98")
    cfg = configkit.build(Cfg, "x", KitConfigError)
    assert cfg.path == "/migrations"
    assert cfg.dsn.get_secret_value() == "postgres://s"
    assert cfg.size == 3


def test_field_without_alias_is_not_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # SECRET/HEADER — обычные переменные окружения; поле без alias'а их не видит,
    # иначе чужой SECRET стал бы общим секретом вебхука.
    monkeypatch.setenv("SECRET", "unrelated-app-secret")
    monkeypatch.setenv("HEADER", "X-From-Env")
    cfg = NoAlias()
    assert cfg.secret == ""
    assert cfg.header == "X-Shared-Secret"


def test_nested_model_field_ignores_its_bare_name(monkeypatch: pytest.MonkeyPatch) -> None:
    # Раньше переменная с именем поля-подмодели уходила в JSON-разбор и валила конфиг.
    monkeypatch.setenv("NESTED", "not-json")
    assert NoAlias().nested.a == 1


def test_env_prefix_does_not_revive_bare_field_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_MAX_CONNS", "7")
    monkeypatch.setenv("PG_PLAIN", "9")
    cfg = Prefixed()
    assert cfg.max_conns == 7
    assert cfg.plain == 0


def test_alias_equal_to_field_name_still_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAME", "from-env")
    assert SelfAliased().same == "from-env"


def test_alias_choices_are_all_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("A_ONE", raising=False)
    monkeypatch.setenv("A_TWO", "two")
    assert Choices().value == "two"


def test_alias_only_subclass_reads_alias_and_ignores_field_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("X_SIZE", "5")
    monkeypatch.setenv("SIZE", "99")
    monkeypatch.setenv("PLAIN", "99")
    cfg = AliasOnly()
    assert cfg.size == 5
    assert cfg.plain == 0


def test_unreadable_env_maps_to_kit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_ITEMS", '["a", ')
    with pytest.raises(KitConfigError) as exc_info:
        configkit.build(Complex, "x", KitConfigError)
    msg = str(exc_info.value)
    assert msg.startswith("x: invalid config: ")
    assert "items" in msg
    assert exc_info.value.__cause__ is None


def test_alias_is_case_insensitive_like_envconfig(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("x_dsn", "postgres://lower")
    monkeypatch.delenv("X_DSN", raising=False)
    assert Cfg().dsn.get_secret_value() == "postgres://lower"


def test_init_by_name_and_by_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_DSN", "env")
    assert Cfg(dsn="name").dsn.get_secret_value() == "name"  # type: ignore[call-arg]
    assert Cfg(X_DSN="alias").dsn.get_secret_value() == "alias"


def test_settings_config_overrides_inherit_strict_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_PATH", "")
    monkeypatch.setenv("PATH", "/usr/bin")
    assert KeepEmpty().path == ""


def test_invalid_maps_to_kit_error_without_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_DSN", "postgres://leak-me")
    monkeypatch.setenv("X_SIZE", "0")
    with pytest.raises(KitConfigError) as exc_info:
        configkit.build(Cfg, "x", KitConfigError)
    msg = str(exc_info.value)
    assert msg.startswith("x: invalid config: ")
    assert "size must be > 0" in msg
    assert "leak-me" not in msg
    assert exc_info.value.__cause__ is None


def test_missing_required_names_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_DSN", raising=False)
    with pytest.raises(KitConfigError, match="X_DSN"):
        configkit.build(Cfg, "x", KitConfigError)


def test_values_override_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_SIZE", "7")
    cfg = configkit.build(Cfg, "x", KitConfigError, dsn="d", size=9)
    assert cfg.size == 9
