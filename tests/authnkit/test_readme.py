"""Быстрый старт README кита исполняется как есть — иначе он протухает молча."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

README = Path(__file__).resolve().parents[2] / "src" / "pylibinfra" / "authnkit" / "README.md"
TESTKIT_README = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "pylibinfra"
    / "testkit"
    / "authntest"
    / "README.md"
)


def _snippet(path: Path, marker: str) -> str:
    blocks = re.findall(r"```python\n(.*?)```", path.read_text(encoding="utf-8"), re.DOTALL)
    return next(block for block in blocks if marker in block)


def _exec(code: str, path: Path) -> dict[str, Any]:
    namespace: dict[str, Any] = {"__name__": "readme"}
    exec(compile(code, str(path), "exec"), namespace)
    return namespace


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "readme-secret-key-of-at-least-32-bytes")
    monkeypatch.setenv("ARGON2_TIME", "1")
    monkeypatch.setenv("ARGON2_MEMORY_KIB", "8192")
    monkeypatch.setenv("ARGON2_THREADS", "1")


async def test_quick_start() -> None:
    await _exec(_snippet(README, "async def main("), README)["main"]()


async def test_dishka_wiring(env: None) -> None:
    await _exec(_snippet(README, "async def wire("), README)["wire"]()


async def test_testkit_readme() -> None:
    await _exec(_snippet(TESTKIT_README, "async def main("), TESTKIT_README)["main"]()
