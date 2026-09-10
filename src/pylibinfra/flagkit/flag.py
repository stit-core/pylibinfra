"""Разбор `FEATURE_FLAGS` и доступ к снятым на старте флагам."""

from __future__ import annotations

from typing import Protocol

from pydantic import Field, model_validator

from pylibinfra._internal import configkit
from pylibinfra._internal.configkit import KitSettings

# Истинными считаются ровно те значения, что принимает strconv.ParseBool в
# golibinfra — иначе один и тот же .env даёт разные kill-switch'и в Go и Python.
_TRUE_VALUES = frozenset({"1", "t", "T", "TRUE", "true", "True"})


class ConfigError(configkit.ConfigError):
    """Невалидная `FEATURE_FLAGS`: запись без `=` либо с пустым именем флага."""


class Config(KitSettings):
    """Конфиг кита: одна строка определений флагов."""

    definitions: str = Field(default="", validation_alias="FEATURE_FLAGS")

    @model_validator(mode="after")
    def _check_definitions(self) -> Config:
        _parse(self.definitions)
        return self


class Store(Protocol):
    """Доступ к снятым на старте флагам по имени."""

    def names(self) -> list[str]:
        """Имена заданных флагов в лексикографическом порядке — для лога конфигурации."""
        ...

    def bool(self, name: str) -> bool:
        """Переключатель: `True` только для `1|t|T|TRUE|true|True`; без флага — `False`."""
        ...

    def str(self, name: str) -> str:
        """Сырое значение флага; без флага — пустая строка."""
        ...


class EnvStore:
    """`Store` поверх разобранного `Config.definitions`; после конструирования не меняется."""

    def __init__(self, cfg: Config) -> None:
        try:
            self._flags = _parse(cfg.definitions)
        except ValueError as exc:
            raise ConfigError(f"flagkit: invalid config: {exc}") from None

    def names(self) -> list[str]:
        """Имена заданных флагов в лексикографическом порядке."""
        return sorted(self._flags)

    def bool(self, name: str) -> bool:
        """Флаг-переключатель: `True` только для `1|t|T|TRUE|true|True`."""
        return self._flags.get(name, "") in _TRUE_VALUES

    def str(self, name: str) -> str:
        """Сырое значение флага; без флага — пустая строка."""
        return self._flags.get(name, "")


def _parse(definitions: str) -> dict[str, str]:
    """Разбирает `name=value`-записи через запятую; ошибки всех записей — в одном ValueError."""
    flags: dict[str, str] = {}
    errors: list[str] = []
    for raw in definitions.split(","):
        entry = raw.strip()
        if not entry:
            continue
        name, sep, value = entry.partition("=")
        if not sep:
            errors.append(f"FEATURE_FLAGS entry {entry!r}: want name=value")
            continue
        name = name.strip()
        if not name:
            errors.append(f"FEATURE_FLAGS entry {entry!r}: empty flag name")
            continue
        flags[name] = value.strip()
    if errors:
        raise ValueError("; ".join(errors))
    return flags


def new_config() -> Config:
    """Читает `FEATURE_FLAGS` из ENV и валидирует формат; невалидная запись — ConfigError."""
    return configkit.build(Config, "flagkit", ConfigError)
