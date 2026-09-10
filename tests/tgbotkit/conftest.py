"""Kit-специфика: конфиг с валидным токеном и in-memory Redis из bottest."""

from __future__ import annotations

import os
from typing import Any

import pytest

from pylibinfra.testkit.bottest import MemoryRedis
from pylibinfra.tgbotkit import Config

TOKEN = "123456:AAstub-token-for-tests"


def cfg(**overrides: Any) -> Config:
    """`Config` с валидным токеном; `overrides` — поля по именам."""
    return Config(token=TOKEN, **overrides)  # type: ignore[arg-type]


@pytest.fixture
def redis_client() -> MemoryRedis:
    return MemoryRedis()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """BOT_*/HTTP_* из окружения разработчика не должны просачиваться в тесты конфига."""
    for name in list(os.environ):
        if name.startswith(("BOT_", "HTTP_")):
            monkeypatch.delenv(name, raising=False)
