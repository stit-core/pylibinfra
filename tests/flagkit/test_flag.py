import pytest

from pylibinfra.flagkit import Config, EnvStore


def test_parses_and_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_FLAGS", "a=true,b=1,c=hello, d = x , bad")
    store = EnvStore(Config())

    assert store.bool("a") is True
    assert store.bool("b") is True
    assert store.bool("c") is False
    assert store.str("c") == "hello"
    assert store.str("d") == "x"  # пробелы вокруг key/val триммятся


def test_missing_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FEATURE_FLAGS", raising=False)
    store = EnvStore(Config())

    assert store.bool("missing") is False
    assert store.str("missing") == ""


def test_pair_without_eq_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_FLAGS", "bad,ok=true")
    store = EnvStore(Config())

    assert store.str("bad") == ""
    assert store.bool("ok") is True
