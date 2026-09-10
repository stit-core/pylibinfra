import asyncio
from collections.abc import Callable

import pytest
from fastapi import FastAPI
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from pylibinfra._internal import requestid
from pylibinfra.httpkit import Config
from pylibinfra.testkit.logtest import Capture

from .conftest import client, entries, one


async def test_request_id_generated_when_absent(
    make_app: Callable[..., FastAPI], log: Capture
) -> None:
    async with client(make_app(Config(), log)) as c:
        resp = await c.get("/ok")
    assert resp.status_code == 200
    assert requestid.valid(resp.headers["x-request-id"])


async def test_request_id_echoes_valid_incoming(
    make_app: Callable[..., FastAPI], log: Capture
) -> None:
    async with client(make_app(Config(), log)) as c:
        resp = await c.get("/ok", headers={"X-Request-ID": "req-42"})
    assert resp.headers["x-request-id"] == "req-42"


async def test_request_id_rejects_invalid_incoming(
    make_app: Callable[..., FastAPI], log: Capture
) -> None:
    async with client(make_app(Config(), log)) as c:
        resp = await c.get("/ok", headers={"X-Request-ID": "a" * 200})
        long_one = resp.headers["x-request-id"]
        resp = await c.get("/ok", headers={"X-Request-ID": "has space"})
        spaced = resp.headers["x-request-id"]
    assert long_one != "a" * 200
    assert spaced != "has space"
    assert requestid.valid(long_one)
    assert requestid.valid(spaced)


async def test_request_id_visible_in_context_and_unbound_after(
    make_app: Callable[..., FastAPI], log: Capture
) -> None:
    seen: list[str] = []
    app = make_app(Config(), log)

    @app.get("/rid")
    async def rid() -> dict[str, str]:
        seen.append(requestid.current())
        return {"rid": seen[-1]}

    async with client(app) as c:
        resp = await c.get("/rid", headers={"X-Request-ID": "req-7"})
    assert seen == ["req-7"]
    assert resp.json() == {"rid": "req-7"}
    assert requestid.current() == ""


async def test_access_log_fields_and_levels(make_app: Callable[..., FastAPI], log: Capture) -> None:
    async with client(make_app(Config(), log)) as c:
        await c.get("/users/7")
        await c.get("/missing")
        await c.get("/boom")

    lines = entries(log, "http")
    assert [r.level for r in lines] == ["info", "warn", "error"]
    ok = lines[0]
    assert ok.value("method") == "GET"
    assert ok.value("path") == "/users/7"
    assert ok.value("route") == "/users/{uid}"
    assert ok.value("status") == 200
    assert ok.value("bytes") > 0
    assert isinstance(ok.value("duration_ms"), float)
    assert lines[1].value("route") == "unmatched"


async def test_access_log_quiet_paths_on_debug(
    make_app: Callable[..., FastAPI], log: Capture
) -> None:
    async with client(make_app(Config(), log)) as c:
        await c.get("/health")
    assert one(log, "http").level == "debug"


async def test_recovery_neutralizes_and_counts(
    make_app: Callable[..., FastAPI],
    log: Capture,
    read_metric: Callable[..., float],
) -> None:
    async with client(make_app(Config(), log)) as c:
        resp = await c.get("/boom")

    assert resp.status_code == 500
    assert resp.json() == {
        "title": "Internal Server Error",
        "status": 500,
        "detail": "internal error",
        "code": "INTERNAL",
    }
    assert "secret-dsn" not in resp.text
    assert "Traceback" not in resp.text
    panic = one(log, "panic recovered")
    assert "secret-dsn" in panic.value("panic")
    assert "Traceback" in panic.value("stack")
    assert read_metric("http_panics_total", {"path": "/boom"}) == 1


async def test_otel_span_name_and_attributes(
    span_exporter: InMemorySpanExporter,
    make_app: Callable[..., FastAPI],
    log: Capture,
) -> None:
    async with client(make_app(Config(), log)) as c:
        await c.get("/users/7")

    spans = span_exporter.get_finished_spans()
    assert [s.name for s in spans] == ["GET /users/{uid}"]
    attrs = dict(spans[0].attributes or {})
    assert attrs["http.route"] == "/users/{uid}"
    assert attrs["http.request.method"] == "GET"
    assert attrs["url.path"] == "/users/7"
    assert attrs["http.response.status_code"] == 200
    assert attrs["request_id"]


async def test_otel_span_skipped_for_quiet_paths(
    span_exporter: InMemorySpanExporter,
    make_app: Callable[..., FastAPI],
    log: Capture,
) -> None:
    async with client(make_app(Config(), log)) as c:
        await c.get("/health")
        await c.get("/ready")
    assert span_exporter.get_finished_spans() == ()


async def test_otel_span_continues_incoming_trace(
    span_exporter: InMemorySpanExporter,
    make_app: Callable[..., FastAPI],
    log: Capture,
) -> None:
    trace_id = "0af7651916cd43dd8448eb211c80319c"
    async with client(make_app(Config(), log)) as c:
        await c.get(
            "/ok",
            headers={"traceparent": f"00-{trace_id}-b7ad6b7169203331-01"},
        )
    span = span_exporter.get_finished_spans()[0]
    assert format(span.context.trace_id, "032x") == trace_id


async def test_chain_order_puts_access_log_inside_span(
    span_exporter: InMemorySpanExporter,
    make_app: Callable[..., FastAPI],
    log: Capture,
) -> None:
    captured: list[str] = []

    class Spy(Capture):
        def _add(self, level: str, msg: str, kv: dict[str, object]) -> None:
            from opentelemetry import trace

            ctx = trace.get_current_span().get_span_context()
            captured.append(format(ctx.trace_id, "032x") if ctx.is_valid else "")
            super()._add(level, msg, kv)

    spy = Spy()
    async with client(make_app(Config(), spy)) as c:
        await c.get("/boom")

    span = span_exporter.get_finished_spans()[0]
    want = format(span.context.trace_id, "032x")
    # panic-log и access-log обязаны нести trace_id: оба работают внутри server-спана
    assert set(captured) == {want}


async def test_recovery_reraises_when_response_already_started(
    make_app: Callable[..., FastAPI],
    log: Capture,
    read_metric: Callable[..., float],
) -> None:
    from collections.abc import AsyncIterator

    from starlette.responses import StreamingResponse

    app = make_app(Config(), log)

    async def broken() -> AsyncIterator[bytes]:
        yield b"first"
        raise RuntimeError("mid-stream")

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        return StreamingResponse(broken(), media_type="text/plain")

    with pytest.raises(RuntimeError, match="mid-stream"):
        async with client(app) as c:
            await c.get("/stream")

    assert one(log, "panic recovered").value("path") == "/stream"
    assert read_metric("http_panics_total", {"path": "/stream"}) == 1


async def test_response_tap_ignores_duplicate_start(log: Capture) -> None:
    from starlette.types import Receive, Scope, Send

    from pylibinfra.httpkit.response import tap

    sent: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        observed = tap(scope, send)
        await observed.send({"type": "http.response.start", "status": 201, "headers": []})
        await observed.send({"type": "http.response.start", "status": 500, "headers": []})
        await observed.send({"type": "http.response.body", "body": b"ok"})
        assert observed.status == 201
        assert observed.body_bytes == 2

    async def record(message: dict[str, object]) -> None:
        sent.append(str(message["type"]))

    await app({"type": "http", "headers": []}, _no_receive, record)
    assert sent == ["http.response.start", "http.response.body"]


async def _no_receive() -> dict[str, object]:
    return {"type": "http.disconnect"}


async def test_streaming_response_is_not_buffered(
    make_app: Callable[..., FastAPI], log: Capture
) -> None:
    from collections.abc import AsyncIterator

    from starlette.responses import StreamingResponse

    app = make_app(Config(), log)
    sent = asyncio.Event()

    async def events() -> AsyncIterator[bytes]:
        for i in range(3):
            yield f"data: {i}\n\n".encode()
            sent.set()
            await asyncio.sleep(0)

    @app.get("/sse")
    async def sse() -> StreamingResponse:
        return StreamingResponse(events(), media_type="text/event-stream")

    async with client(app) as c, c.stream("GET", "/sse") as resp:
        body = b"".join([chunk async for chunk in resp.aiter_bytes()])
        assert resp.status_code == 200
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-request-id"]

    assert body == b"data: 0\n\ndata: 1\n\ndata: 2\n\n"
    assert one(log, "http").value("bytes") == len(body)
