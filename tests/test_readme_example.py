"""Сниппет корневого README § Observability собирается целиком (аналог fx.ValidateApp).

dishka валидирует граф на make_async_container: каждая зависимость каждого
провайдера разрешима. Ресурсы (пул, NATS, Redis) не создаются — их поднимает
первый get(), которого здесь нет.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from dishka import AsyncContainer

README = Path(__file__).resolve().parents[1] / "README.md"


def _snippet() -> str:
    blocks = re.findall(r"```python\n(.*?)```", README.read_text(encoding="utf-8"), re.S)
    for block in blocks:
        if "make_async_container(" in block:
            return block
    raise AssertionError("README: python-блок с make_async_container не найден")


@pytest.mark.filterwarnings("error::UserWarning")
async def test_readme_container_graph_is_valid(monkeypatch: Any) -> None:
    monkeypatch.setenv("PG_DSN", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "readme")
    namespace: dict[str, Any] = {}
    exec(compile(_snippet(), str(README), "exec"), namespace)
    container = namespace["container"]
    assert isinstance(container, AsyncContainer)
    await container.close()
