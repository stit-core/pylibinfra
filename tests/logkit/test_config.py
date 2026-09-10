from __future__ import annotations

import pytest
from pydantic import ValidationError

from pylibinfra.logkit import (
    Config,
    ConfigError,
    RedactConfig,
    ServiceConfig,
    new_config,
    new_redact_config,
    new_service_config,
)
from pylibinfra.logkit.config import parse_format, parse_level, split_patterns


def test_defaults_are_valid() -> None:
    cfg = new_config()

    assert (cfg.level, cfg.format, cfg.source, cfg.silent) == ("info", "json", False, False)


def test_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "WARN")
    monkeypatch.setenv("LOG_FORMAT", "TEXT")
    monkeypatch.setenv("LOG_SOURCE", "true")
    monkeypatch.setenv("LOG_SILENT", "true")

    cfg = new_config()

    assert (cfg.level, cfg.format, cfg.source, cfg.silent) == ("warn", "text", True, True)


def test_empty_level_means_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "")

    assert new_config().level == "info"
    assert parse_level("") == parse_level("info")
    assert parse_format("") == "json"


@pytest.mark.parametrize(("env", "value"), [("LOG_LEVEL", "verbose"), ("LOG_FORMAT", "yaml")])
def test_invalid_values_are_rejected(monkeypatch: pytest.MonkeyPatch, env: str, value: str) -> None:
    monkeypatch.setenv(env, value)

    with pytest.raises(ConfigError, match=f"logkit: invalid config:.*{env}"):
        new_config()


def test_direct_construction_raises_pydantic_error() -> None:
    with pytest.raises(ValidationError):
        Config(level="nope")


def test_service_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SERVICE_NAME", "api")
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("OTEL_SERVICE_VERSION", "v1.0.0")

    cfg = new_service_config()

    assert (cfg.service, cfg.env, cfg.version) == ("api", "prod", "v1.0.0")


def test_service_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in ("OTEL_SERVICE_NAME", "APP_ENV", "OTEL_SERVICE_VERSION"):
        monkeypatch.delenv(env, raising=False)

    cfg = ServiceConfig()

    assert (cfg.service, cfg.env, cfg.version) == ("", "dev", "dev")


def test_patterns_split_on_semicolon() -> None:
    assert split_patterns(r"a{10,15}; b\;c ;") == ("a{10,15}", r"b\;c")
    assert split_patterns("") == ()


def test_redact_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_REDACT_ENABLED", "true")
    monkeypatch.setenv("LOG_REDACT_PATTERNS", r"secret-\d+;token-\w+")
    monkeypatch.setenv("LOG_REDACT_REPLACE_WITH", "***")

    cfg = new_redact_config()

    assert cfg.enabled is True
    assert cfg.patterns == (r"secret-\d+", r"token-\w+")
    assert cfg.replace_with == "***"


def test_redact_defaults() -> None:
    cfg = new_redact_config()

    assert cfg.enabled is False
    assert cfg.patterns == ()
    assert cfg.replace_with == "[REDACTED]"


@pytest.mark.parametrize("pattern", ["(", "a*", "x?", "secret|"])
def test_invalid_pattern_is_rejected(monkeypatch: pytest.MonkeyPatch, pattern: str) -> None:
    monkeypatch.setenv("LOG_REDACT_PATTERNS", pattern)

    with pytest.raises(ConfigError, match="LOG_REDACT_PATTERNS"):
        new_redact_config()


@pytest.mark.parametrize("replace_with", ['"', "\\", "a\tb"])
def test_invalid_replacement_is_rejected(
    monkeypatch: pytest.MonkeyPatch, replace_with: str
) -> None:
    monkeypatch.setenv("LOG_REDACT_REPLACE_WITH", replace_with)

    with pytest.raises(ConfigError, match="LOG_REDACT_REPLACE_WITH"):
        new_redact_config()


def test_config_is_frozen() -> None:
    cfg = RedactConfig()

    with pytest.raises(ValidationError):
        cfg.enabled = True  # type: ignore[misc]
