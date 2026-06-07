import httpx
import pytest

from pylibinfra.httpclient import Client, Config, request_id


def _cfg(**over: object) -> Config:
    base: dict[str, object] = {
        "HTTPCLIENT_TIMEOUT": "5.0",
        "HTTPCLIENT_MAX_RETRIES": "3",
        "HTTPCLIENT_RETRY_WAIT": "0.0",
    }
    base.update(over)
    return Config(**base)  # type: ignore[arg-type]


def _client(handler: object, cfg: Config) -> Client:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    http = httpx.AsyncClient(transport=transport, base_url="http://svc")
    return Client(cfg, http)


async def test_200_no_retry() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    c = _client(handler, _cfg())
    resp = await c.get("http://svc/ping")
    await c.aclose()

    assert resp.status_code == 200
    assert calls == 1


async def test_5xx_retries_then_success() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503) if calls < 3 else httpx.Response(200)

    c = _client(handler, _cfg(HTTPCLIENT_MAX_RETRIES="3"))
    resp = await c.get("http://svc/ping")
    await c.aclose()

    assert resp.status_code == 200
    assert calls == 3


async def test_5xx_exhausted_retries_raises() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(502)

    c = _client(handler, _cfg(HTTPCLIENT_MAX_RETRIES="2"))
    with pytest.raises(httpx.HTTPStatusError):
        await c.get("http://svc/ping")
    await c.aclose()


async def test_transport_error_retries() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 2:
            raise httpx.ConnectError("boom")
        return httpx.Response(200)

    c = _client(handler, _cfg(HTTPCLIENT_MAX_RETRIES="3"))
    resp = await c.get("http://svc/ping")
    await c.aclose()

    assert resp.status_code == 200
    assert calls == 2


async def test_request_id_forwarded_from_contextvar() -> None:
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["id"] = req.headers.get("X-Request-ID", "")
        return httpx.Response(200)

    token = request_id.set("ctx-req-id")
    try:
        c = _client(handler, _cfg(HTTPCLIENT_MAX_RETRIES="0"))
        await c.get("http://svc/ping")
        await c.aclose()
    finally:
        request_id.reset(token)

    assert seen["id"] == "ctx-req-id"


async def test_explicit_request_id_wins_over_contextvar() -> None:
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["id"] = req.headers.get("X-Request-ID", "")
        return httpx.Response(200)

    token = request_id.set("ctx-req-id")
    try:
        c = _client(handler, _cfg(HTTPCLIENT_MAX_RETRIES="0"))
        await c.get("http://svc/ping", headers={"X-Request-ID": "explicit-id"})
        await c.aclose()
    finally:
        request_id.reset(token)

    assert seen["id"] == "explicit-id"


async def test_no_request_id_when_contextvar_empty() -> None:
    seen: dict[str, str | None] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["id"] = req.headers.get("X-Request-ID")
        return httpx.Response(200)

    c = _client(handler, _cfg(HTTPCLIENT_MAX_RETRIES="0"))
    await c.get("http://svc/ping")
    await c.aclose()

    assert seen["id"] is None
