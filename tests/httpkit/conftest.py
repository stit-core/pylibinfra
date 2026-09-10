"""Kit-специфика: capture-логгер из logtest и сборка тестового приложения httpkit."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from pylibinfra.httpkit import Config, build_app
from pylibinfra.logkit import Logger
from pylibinfra.testkit.logtest import Capture, Entry


@pytest.fixture(autouse=True)
def _fresh_meter(metric_reader: object) -> object:
    """Каждый тест кита получает свой MeterProvider: инструменты создаются после его установки."""
    return metric_reader


@pytest.fixture
def log() -> Capture:
    return Capture()


def entries(log: Capture, msg: str) -> list[Entry]:
    return [entry for entry in log.entries() if entry.msg == msg]


def one(log: Capture, msg: str) -> Entry:
    found = entries(log, msg)
    assert len(found) == 1, f"want exactly one {msg!r}, got {found}"
    return found[0]


def client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


def routed(cfg: Config, log: Logger, **kwargs: Any) -> FastAPI:
    """Приложение кита с типовым набором роутов для тестов цепочки."""
    app = build_app(cfg, log, **kwargs)

    @app.get("/ok")
    async def ok() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/users/{uid}")
    async def user(uid: str) -> dict[str, str]:
        return {"id": uid}

    @app.get("/boom")
    async def boom() -> dict[str, bool]:
        raise RuntimeError("kaboom: secret-dsn")

    @app.post("/echo")
    async def echo(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"got": payload or {}}

    return app


@pytest.fixture
def make_app() -> Callable[..., FastAPI]:
    return routed
