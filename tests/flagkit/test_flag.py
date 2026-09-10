import pytest
from pydantic import ValidationError

from pylibinfra.flagkit import Config, ConfigError, EnvStore, Store, new_config


def store(definitions: str) -> Store:
    return EnvStore(Config(definitions=definitions))


def test_parses_names_values_and_spaces() -> None:
    flags = store("a=true,b=1,c=hello, d = x ")

    assert flags.bool("a") is True
    assert flags.bool("b") is True
    assert flags.bool("c") is False
    assert flags.str("c") == "hello"
    assert flags.str("d") == "x"
    assert flags.names() == ["a", "b", "c", "d"]


def test_missing_flag_defaults() -> None:
    flags = store("")

    assert flags.bool("missing") is False
    assert flags.str("missing") == ""
    assert flags.names() == []


@pytest.mark.parametrize("value", ["1", "t", "T", "TRUE", "true", "True"])
def test_true_values(value: str) -> None:
    assert store(f"f={value}").bool("f") is True


@pytest.mark.parametrize(
    "value", ["0", "f", "F", "FALSE", "false", "False", "yes", "on", "TrUe", ""]
)
def test_false_values(value: str) -> None:
    assert store(f"f={value}").bool("f") is False


def test_last_entry_wins() -> None:
    assert store("foo=true,foo=false").bool("foo") is False


def test_quotes_are_not_stripped() -> None:
    flags = store('foo="true"')

    assert flags.str("foo") == '"true"'
    assert flags.bool("foo") is False


def test_value_keeps_everything_after_first_eq() -> None:
    assert store("dsn=k=v=w").str("dsn") == "k=v=w"


def test_semicolon_is_part_of_value() -> None:
    flags = store("foo=true;bar=baz")

    assert flags.names() == ["foo"]
    assert flags.str("foo") == "true;bar=baz"


def test_empty_entries_skipped() -> None:
    assert store(",a=1,,b=2,").names() == ["a", "b"]


def test_names_are_case_sensitive() -> None:
    flags = store("FOO=true")

    assert flags.bool("foo") is False
    assert flags.bool("FOO") is True


def test_entry_without_eq_is_rejected() -> None:
    with pytest.raises(ValidationError, match="want name=value"):
        Config(definitions="bad,ok=true")


def test_entry_with_empty_name_is_rejected() -> None:
    with pytest.raises(ValidationError, match="empty flag name"):
        Config(definitions="=value")


def test_all_bad_entries_reported_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_FLAGS", "bad, =v ,ok=true")

    with pytest.raises(ConfigError) as exc_info:
        new_config()

    message = str(exc_info.value)
    assert "flagkit: invalid config" in message
    # Имя переменной в тексте: оператор чинит окружение, а не поле модели.
    assert "FEATURE_FLAGS entry 'bad'" in message
    assert "empty flag name" in message


def test_env_store_rejects_unvalidated_config() -> None:
    # model_construct обходит валидатор — стор обязан проверить строку сам.
    with pytest.raises(ConfigError, match="want name=value"):
        EnvStore(Config.model_construct(definitions="bad"))


def test_config_error_is_value_error() -> None:
    assert issubclass(ConfigError, ValueError)


def test_new_config_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_FLAGS", "new_checkout=true,region=eu")

    flags = EnvStore(new_config())

    assert flags.bool("new_checkout") is True
    assert flags.str("region") == "eu"
    assert flags.names() == ["new_checkout", "region"]


def test_new_config_defaults_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FEATURE_FLAGS", raising=False)

    assert new_config().definitions == ""


def test_config_is_frozen() -> None:
    cfg = Config(definitions="a=1")

    with pytest.raises(ValidationError):
        cfg.definitions = "b=2"
