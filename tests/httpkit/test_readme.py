"""Сниппеты README кита должны работать: quick-start собирается и отвечает."""

import re
from pathlib import Path

import httpx
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import healthkit, httpkit
from pylibinfra.logkit import Logger, nop

_README = Path(httpkit.__file__).parent / "README.md"


async def ready() -> None:
    return None


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def http_config(self) -> httpkit.Config:
        return httpkit.new_config()

    @provide
    def logger(self) -> Logger:
        return nop()

    @provide
    def probes(self) -> healthkit.Probes:
        return (healthkit.new("app", ready),)


async def test_quick_start_snippet_works(monkeypatch) -> None:
    monkeypatch.setenv("HTTP_ADDR", "127.0.0.1:0")
    container = make_async_container(httpkit.HttpProvider(), AppProvider())
    server = await container.get(httpkit.Server)

    @server.app.get("/users/{uid}")
    async def get_user(uid: str) -> dict[str, str]:
        if uid == "missing":
            raise httpkit.not_found("USER_NOT_FOUND", "user not found")
        return {"id": uid}

    async with httpx.AsyncClient(base_url=f"http://{server.addr()}") as c:
        assert (await c.get("/users/7")).json() == {"id": "7"}
        missing = await c.get("/users/missing")
        assert missing.status_code == 404
        assert missing.json()["code"] == "USER_NOT_FOUND"
        assert (await c.get("/ready")).json() == {
            "status": "ok",
            "probes": [{"name": "app", "status": "ok"}],
        }

    await container.close()


async def test_no_di_snippet_works() -> None:
    server = httpkit.Server(httpkit.Config(addr="127.0.0.1:0"), nop(), probes=())
    server.listen()
    addr = server.addr()

    import asyncio

    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server._uvicorn.started:
            break
        await asyncio.sleep(0.01)

    async with httpx.AsyncClient(base_url=f"http://{addr}") as c:
        assert (await c.get("/health")).json() == {"status": "ok"}

    await server.stop()
    await task


def test_readme_lists_docs_pages() -> None:
    text = _README.read_text()
    docs = Path(httpkit.__file__).parent / "docs"
    for page in ("architecture", "configuration", "observability", "usage", "testing"):
        assert f"docs/{page}.md" in text
        assert (docs / f"{page}.md").exists()


def test_readme_exports_are_public() -> None:
    text = _README.read_text()
    for name in ("httpkit.Config", "httpkit.HttpProvider", "httpkit.Server", "httpkit.new_config"):
        assert name in text
        assert name.split(".", 1)[1] in httpkit.__all__


def _pages() -> str:
    docs = Path(httpkit.__file__).parent / "docs"
    return "".join(p.read_text() for p in (_README, *sorted(docs.glob("*.md"))))


def test_docs_mention_every_export() -> None:
    missing = [name for name in httpkit.__all__ if name not in _pages()]

    assert missing == []


def test_configuration_doc_lists_every_env_alias() -> None:
    text = (Path(httpkit.__file__).parent / "docs" / "configuration.md").read_text()
    documented = set(re.findall(r"`(HTTP_[A-Z_0-9]+)`", text))
    models = (
        httpkit.Config,
        httpkit.CORSConfig,
        httpkit.RateLimitConfig,
        httpkit.MaxBodyConfig,
        httpkit.SecurityHeadersConfig,
        httpkit.IdempotencyConfig,
    )
    declared = {
        str(field.validation_alias)
        for model in models
        for field in model.model_fields.values()
        if isinstance(field.validation_alias, str)
    }

    assert declared <= documented
    # обратная сторона: в таблице нет переменных, которых кит не читает
    assert documented <= declared
