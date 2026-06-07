import httpx
import pytest
from fastapi import FastAPI

from pylibinfra.httpkit import Config, build_app, new_instruments
from pylibinfra.logkit import nop


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


def _app(cfg: Config) -> FastAPI:
    app = build_app(cfg, nop(), instruments=new_instruments())

    @app.get("/ok")
    async def ok() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/boom")
    async def boom() -> dict[str, bool]:
        raise RuntimeError("kaboom")

    @app.post("/echo")
    async def echo() -> dict[str, bool]:
        return {"ok": True}

    return app


async def test_request_id_present_in_response() -> None:
    async with _client(_app(Config(_env_file=None))) as c:
        r = await c.get("/ok")
        assert r.status_code == 200
        assert r.headers.get("x-request-id")


async def test_request_id_echoes_incoming() -> None:
    async with _client(_app(Config(_env_file=None))) as c:
        r = await c.get("/ok", headers={"X-Request-ID": "fixed-id"})
        assert r.headers["x-request-id"] == "fixed-id"


async def test_recovery_returns_500_api_error() -> None:
    async with _client(_app(Config(_env_file=None))) as c:
        r = await c.get("/boom")
        assert r.status_code == 500
        body = r.json()
        assert body["status"] == 500
        assert body["code"] == "INTERNAL"
        # стек-трейс наружу не утекает
        assert "kaboom" not in r.text
        assert "Traceback" not in r.text


async def test_security_headers_present() -> None:
    cfg = Config(_env_file=None, security={"csp": "default-src 'self'"})
    async with _client(_app(cfg)) as c:
        r = await c.get("/ok")
        assert r.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert r.headers["content-security-policy"] == "default-src 'self'"


async def test_security_headers_csp_omitted_when_empty() -> None:
    async with _client(_app(Config(_env_file=None))) as c:
        r = await c.get("/ok")
        assert "content-security-policy" not in r.headers


async def test_max_body_rejects_large() -> None:
    cfg = Config(_env_file=None, max_body={"max_bytes": 100})
    async with _client(_app(cfg)) as c:
        r = await c.post("/echo", content=b"a" * 200)
        assert r.status_code == 413
        assert r.json()["code"] == "PAYLOAD_TOO_LARGE"


async def test_max_body_allows_small() -> None:
    cfg = Config(_env_file=None, max_body={"max_bytes": 100})
    async with _client(_app(cfg)) as c:
        r = await c.post("/echo", content=b"a" * 10)
        assert r.status_code == 200


async def test_max_body_disabled_when_zero() -> None:
    cfg = Config(_env_file=None, max_body={"max_bytes": 0})
    async with _client(_app(cfg)) as c:
        r = await c.post("/echo", content=b"a" * 5000)
        assert r.status_code == 200


async def test_rate_limit_rejects_over_burst() -> None:
    cfg = Config(_env_file=None, rate_limit={"rps": 1, "burst": 2})
    async with _client(_app(cfg)) as c:
        statuses = [(await c.get("/ok")).status_code for _ in range(5)]
    assert 429 in statuses
    assert statuses[0] == 200


async def test_rate_limit_disabled_when_zero() -> None:
    cfg = Config(_env_file=None, rate_limit={"rps": 0})
    async with _client(_app(cfg)) as c:
        statuses = [(await c.get("/ok")).status_code for _ in range(10)]
    assert all(s == 200 for s in statuses)


async def test_cors_preflight() -> None:
    cfg = Config(_env_file=None, cors={"origins": "https://example.com"})
    async with _client(_app(cfg)) as c:
        r = await c.options(
            "/ok",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r.headers.get("access-control-allow-origin") == "https://example.com"


@pytest.mark.parametrize("path", ["/ok"])
async def test_cors_disabled_no_header(path: str) -> None:
    async with _client(_app(Config(_env_file=None))) as c:
        r = await c.get(path, headers={"Origin": "https://example.com"})
        assert "access-control-allow-origin" not in r.headers
