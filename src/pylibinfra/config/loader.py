"""Композиция источников значений поверх ENV-слоя pydantic-settings."""

from __future__ import annotations

import os
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from pylibinfra._internal import configkit


class ConfigError(configkit.ConfigError):
    """Источник не отработал либо модель не прошла валидацию."""


class Source(Protocol):
    """Источник значений: кладёт их в `dst` под именами env-алиасов модели."""

    def apply(self, dst: dict[str, Any]) -> None:
        """Дописывает свои значения в `dst`; поздний источник перезатирает ранний."""
        ...


def load[T: BaseModel](model: type[T], *sources: Source) -> T:
    """Строит модель из ENV, поверх — значения источников по порядку.

    Применяются **все** источники: ошибки собираются и поднимаются одним
    `ConfigError` со строкой `source[i]: …` на каждый упавший. Ошибка валидации
    модели перечисляет локации полей без их значений.
    """
    values: dict[str, Any] = {}
    failures: list[str] = []
    for i, source in enumerate(sources):
        try:
            source.apply(values)
        except Exception as exc:
            failures.append(f"source[{i}]: {exc}")
    if failures:
        raise ConfigError("config: " + "; ".join(failures))

    try:
        return model(**values)
    except ValidationError as exc:
        raise ConfigError(configkit.describe("config", exc)) from None


def env_prefixed(prefix: str) -> Source:
    """Источник ENV с приставкой: `<PREFIX>_<NAME>` перекрывает голую `<NAME>`."""
    return _EnvPrefixed(prefix)


class _EnvPrefixed:
    def __init__(self, prefix: str) -> None:
        cleaned = prefix.strip().strip("_").upper()
        if not cleaned:
            raise ConfigError("config: env_prefixed: empty prefix")
        self._prefix = f"{cleaned}_"

    def apply(self, dst: dict[str, Any]) -> None:
        for key, value in os.environ.items():
            name = key.upper()
            if name.startswith(self._prefix) and len(name) > len(self._prefix):
                dst[name.removeprefix(self._prefix)] = value
