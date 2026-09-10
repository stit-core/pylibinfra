import asyncio
from collections.abc import AsyncIterator, Callable

import pytest
from fastapi import FastAPI, Request
from starlette.responses import StreamingResponse
from starlette.types import Message, Receive, Scope, Send

from pylibinfra.httpkit import Config, MaxBodyConfig, MaxBodyError, MaxBodyMiddleware
from pylibinfra.testkit.logtest import Capture

from .conftest import client


def _app(cfg: Config, log: Capture, make_app: Callable[..., FastAPI]) -> FastAPI:
    app = make_app(cfg, log)

    @app.post("/stream")
    async def stream(request: Request) -> dict[str, int]:
        total = 0
        try:
            async for chunk in request.stream():
                total += len(chunk)
        except MaxBodyError:
            return {"size": -1}
        return {"size": total}

    @app.post("/raw")
    async def raw(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    @app.post("/grouped")
    async def grouped(request: Request) -> dict[str, int]:
        async with asyncio.TaskGroup() as group:
            task = group.create_task(request.body())
        return {"size": len(task.result())}

    @app.post("/echo-stream")
    async def echo_stream(request: Request) -> StreamingResponse:
        async def gen() -> AsyncIterator[bytes]:
            async for chunk in request.stream():
                yield chunk

        return StreamingResponse(gen())

    return app


async def _chunks(payload: bytes, size: int) -> AsyncIterator[bytes]:
    for start in range(0, len(payload), size):
        yield payload[start : start + size]


async def test_rejects_declared_content_length(
    make_app: Callable[..., FastAPI],
    log: Capture,
    read_metric: Callable[..., float],
) -> None:
    cfg = Config(max_body={"max_bytes": 100})
    async with client(make_app(cfg, log)) as c:
        resp = await c.post("/echo", content=b"a" * 200)

    assert resp.status_code == 413
    assert resp.json()["code"] == "PAYLOAD_TOO_LARGE"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert read_metric("http_max_body_rejections_total", {"path": "unmatched"}) == 1


async def test_allows_small_body(make_app: Callable[..., FastAPI], log: Capture) -> None:
    cfg = Config(max_body={"max_bytes": 100})
    async with client(make_app(cfg, log)) as c:
        resp = await c.post("/echo", json={"a": 1})
    assert resp.status_code == 200
    assert resp.json() == {"got": {"a": 1}}


async def test_disabled_when_zero(make_app: Callable[..., FastAPI], log: Capture) -> None:
    cfg = Config(max_body={"max_bytes": 0})
    async with client(make_app(cfg, log)) as c:
        resp = await c.post("/echo", json={"a": "x" * 5000})
    assert resp.status_code == 200


async def test_chunked_overflow_raises_in_receive(
    make_app: Callable[..., FastAPI],
    log: Capture,
    read_metric: Callable[..., float],
) -> None:
    cfg = Config(max_body={"max_bytes": 50})
    app = _app(cfg, log, make_app)
    async with client(app) as c:
        resp = await c.post("/stream", content=_chunks(b"a" * 200, 20))

    assert resp.status_code == 200
    assert resp.json() == {"size": -1}
    assert read_metric("http_max_body_rejections_total") == 1


async def test_uncaught_overflow_becomes_413(
    make_app: Callable[..., FastAPI],
    log: Capture,
    read_metric: Callable[..., float],
) -> None:
    cfg = Config(max_body={"max_bytes": 50})
    app = _app(cfg, log, make_app)
    # обработчик читает тело сам и MaxBodyError не ловит: ответ рисует middleware
    async with client(app) as c:
        resp = await c.post("/raw", content=_chunks(b"a" * 200, 20))

    assert resp.status_code == 413
    assert resp.json()["code"] == "PAYLOAD_TOO_LARGE"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert read_metric("http_max_body_rejections_total") == 1


async def test_overflow_inside_task_group_still_answers_413(
    make_app: Callable[..., FastAPI],
    log: Capture,
    read_metric: Callable[..., float],
) -> None:
    cfg = Config(max_body={"max_bytes": 50})
    app = _app(cfg, log, make_app)
    # tasks-group заворачивает MaxBodyError в ExceptionGroup — 413 всё равно доезжает
    async with client(app) as c:
        resp = await c.post("/grouped", content=_chunks(b"a" * 200, 20))

    assert resp.status_code == 413
    assert resp.json()["code"] == "PAYLOAD_TOO_LARGE"
    assert read_metric("http_max_body_rejections_total") == 1


async def test_overflow_after_response_started_is_not_overwritten(
    make_app: Callable[..., FastAPI],
    log: Capture,
    read_metric: Callable[..., float],
) -> None:
    cfg = Config(max_body={"max_bytes": 50})
    app = _app(cfg, log, make_app)
    # ответ уже начат: второй раз статус не написать, ошибка уходит наверх
    # (Starlette заворачивает её в группу задач стриминга)
    async with client(app) as c:
        with pytest.raises(BaseExceptionGroup) as err:
            await c.post("/echo-stream", content=_chunks(b"a" * 200, 20))

    assert err.group_contains(MaxBodyError)
    assert read_metric("http_max_body_rejections_total") == 1


async def test_started_response_is_not_replaced_by_413() -> None:
    async def raw_app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        while True:
            message = await receive()
            if not message.get("more_body", False):
                return

    middleware = MaxBodyMiddleware(raw_app, MaxBodyConfig(max_bytes=50))
    scope: Scope = {"type": "http", "method": "POST", "path": "/raw", "headers": []}
    chunks = [
        {"type": "http.request", "body": b"a" * 40, "more_body": True},
        {"type": "http.request", "body": b"a" * 40, "more_body": False},
    ]
    sent: list[Message] = []

    async def receive() -> Message:
        return chunks.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    with pytest.raises(MaxBodyError):
        await middleware(scope, receive, send)

    assert [m["type"] for m in sent] == ["http.response.start"]
    assert sent[0]["status"] == 200


async def test_chunked_overflow_still_answers_413_for_json_route(
    make_app: Callable[..., FastAPI],
    log: Capture,
    read_metric: Callable[..., float],
) -> None:
    cfg = Config(max_body={"max_bytes": 50})
    async with client(make_app(cfg, log)) as c:
        resp = await c.post("/echo", content=_chunks(b'{"a": "' + b"x" * 200 + b'"}', 20))

    assert resp.status_code == 413
    assert resp.json()["code"] == "PAYLOAD_TOO_LARGE"
    assert read_metric("http_max_body_rejections_total") == 1
