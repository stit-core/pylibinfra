from collections.abc import Callable

from fastapi import FastAPI

from pylibinfra.httpkit import Config
from pylibinfra.testkit.logtest import Capture

from .conftest import client, one


async def test_security_headers_on_every_response(
    make_app: Callable[..., FastAPI], log: Capture
) -> None:
    cfg = Config(security={"csp": "default-src 'self'"})
    async with client(make_app(cfg, log)) as c:
        ok = await c.get("/ok")
        panic = await c.get("/boom")

    for resp in (ok, panic):
        assert resp.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-frame-options"] == "DENY"
        assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert resp.headers["content-security-policy"] == "default-src 'self'"


async def test_empty_header_value_is_not_set(
    make_app: Callable[..., FastAPI], log: Capture
) -> None:
    cfg = Config(security={"hsts": "", "frame_options": ""})
    async with client(make_app(cfg, log)) as c:
        resp = await c.get("/ok")
    assert "content-security-policy" not in resp.headers
    assert "strict-transport-security" not in resp.headers
    assert "x-frame-options" not in resp.headers


async def test_cors_disabled_without_origins(
    make_app: Callable[..., FastAPI], log: Capture
) -> None:
    async with client(make_app(Config(), log)) as c:
        resp = await c.get("/ok", headers={"Origin": "https://example.com"})
    assert "access-control-allow-origin" not in resp.headers
    assert "vary" not in resp.headers


async def test_cors_echoes_listed_origin_with_vary(
    make_app: Callable[..., FastAPI], log: Capture
) -> None:
    cfg = Config(cors={"origins": ["https://app.example"], "allow_credentials": True})
    async with client(make_app(cfg, log)) as c:
        resp = await c.get("/ok", headers={"Origin": "https://app.example"})

    assert resp.headers["access-control-allow-origin"] == "https://app.example"
    assert resp.headers["access-control-allow-credentials"] == "true"
    assert resp.headers["vary"] == "Origin"
    assert resp.headers["access-control-expose-headers"] == (
        "X-Request-ID, Location, Retry-After, Idempotent-Replayed"
    )


async def test_cors_unlisted_origin_gets_no_acao(
    make_app: Callable[..., FastAPI], log: Capture
) -> None:
    cfg = Config(cors={"origins": ["https://app.example"]})
    async with client(make_app(cfg, log)) as c:
        resp = await c.get("/ok", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in resp.headers
    assert resp.headers["vary"] == "Origin"


async def test_cors_wildcard_never_sends_credentials(
    make_app: Callable[..., FastAPI], log: Capture
) -> None:
    cfg = Config(cors={"origins": ["*"]})
    async with client(make_app(cfg, log)) as c:
        resp = await c.get("/ok", headers={"Origin": "https://any.example"})
    assert resp.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in resp.headers


async def test_cors_preflight_answers_204_with_security_headers(
    make_app: Callable[..., FastAPI], log: Capture
) -> None:
    cfg = Config(cors={"origins": ["https://app.example"], "max_age": 120})
    async with client(make_app(cfg, log)) as c:
        resp = await c.options(
            "/ok",
            headers={
                "Origin": "https://app.example",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert resp.status_code == 204
    assert resp.headers["access-control-allow-methods"] == "GET, POST, PUT, PATCH, DELETE, OPTIONS"
    assert resp.headers["access-control-allow-headers"] == (
        "Authorization, Content-Type, X-Request-ID"
    )
    assert resp.headers["access-control-max-age"] == "120"
    assert resp.headers["x-content-type-options"] == "nosniff"


async def test_preflight_is_counted_and_logged(
    make_app: Callable[..., FastAPI],
    log: Capture,
    read_metric: Callable[..., float],
) -> None:
    cfg = Config(cors={"origins": ["https://app.example"]})
    async with client(make_app(cfg, log)) as c:
        await c.options("/ok", headers={"Origin": "https://app.example"})
    assert read_metric("http_requests_total", {"status": "204", "path": "unmatched"}) == 1
    assert one(log, "http").value("status") == 204


async def test_docs_disabled(make_app: Callable[..., FastAPI], log: Capture) -> None:
    async with client(make_app(Config(docs_enabled=False), log)) as c:
        assert (await c.get("/docs")).status_code == 404
        assert (await c.get("/redoc")).status_code == 404
        assert (await c.get("/openapi.json")).status_code == 404

    async with client(make_app(Config(), log)) as c:
        assert (await c.get("/docs")).status_code == 200
        assert (await c.get("/openapi.json")).status_code == 200


async def test_non_http_scope_passes_through(log: Capture) -> None:
    from starlette.types import Receive, Scope, Send

    from pylibinfra.httpkit import CORSConfig, CORSMiddleware, SecurityHeadersConfig
    from pylibinfra.httpkit import SecurityHeadersMiddleware as Sec

    seen: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    async def noop() -> dict[str, str]:
        return {"type": "websocket.connect"}

    async def send(message: dict[str, object]) -> None:
        return None

    await CORSMiddleware(app, CORSConfig(origins=["*"]))({"type": "websocket"}, noop, send)
    await Sec(app, SecurityHeadersConfig())({"type": "lifespan"}, noop, send)
    assert seen == ["websocket", "lifespan"]
