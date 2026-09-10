from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import BaseModel

from pylibinfra.httpclient import (
    ApiClient,
    ApiClients,
    AuthError,
    BreakerOpenError,
    Client,
    ConflictError,
    ContractError,
    CrossHostRedirectError,
    ForbiddenError,
    InvalidRequestError,
    NotFoundError,
    RateLimitedError,
    UnavailableError,
    UnknownBackendError,
)

from .conftest import Received, cfg, mock_client, respond, serve


def api(
    handler: object,
    **over: object,
) -> ApiClient:
    over.setdefault("max_retries", 0)
    over.setdefault("breaker", {"failure_threshold": 0})
    return ApiClient(mock_client(handler, cfg(**over)))  # type: ignore[arg-type]


class _User(BaseModel):
    id: str
    name: str


async def test_2xx_json_body() -> None:
    client = api(lambda _: httpx.Response(200, json={"id": "42", "name": "n"}))

    assert await client.get("/v1/users/42") == {"id": "42", "name": "n"}
    await client.aclose()


async def test_2xx_empty_body_is_none() -> None:
    client = api(lambda _: httpx.Response(204))

    assert await client.post("/v1/orders/1/fulfill", idempotency_key="k") is None
    await client.aclose()


async def test_2xx_non_json_body_raises_contract_error() -> None:
    client = api(lambda _: httpx.Response(200, text="<html>WAF</html>"))

    with pytest.raises(ContractError, match="non-JSON") as err:
        await client.get("/v1/users/42")
    await client.aclose()

    assert isinstance(err.value, UnavailableError)
    assert err.value.body_snippet.startswith("<html>")


async def test_model_decodes_into_wire_model() -> None:
    client = api(lambda _: httpx.Response(200, json={"id": "42", "name": "n"}))

    assert await client.get("/v1/users/42", model=_User) == _User(id="42", name="n")
    await client.aclose()


async def test_model_mismatch_raises_contract_error() -> None:
    client = api(lambda _: httpx.Response(200, json={"id": "42"}))

    with pytest.raises(ContractError, match="_User") as err:
        await client.get("/v1/users/42", model=_User)
    await client.aclose()

    assert err.value.__cause__ is not None


async def test_404_raises_not_found_with_context() -> None:
    client = api(lambda _: httpx.Response(404))

    with pytest.raises(NotFoundError) as err:
        await client.get("/v1/users/404")
    await client.aclose()

    assert err.value.status_code == 404
    assert err.value.method == "GET"
    assert err.value.path == "/v1/users/404"


async def test_409_raises_conflict() -> None:
    client = api(lambda _: httpx.Response(409))

    with pytest.raises(ConflictError):
        await client.post("/v1/orders", json={}, idempotency_key="k")
    await client.aclose()


async def test_401_raises_auth_error() -> None:
    client = api(lambda _: httpx.Response(401))

    with pytest.raises(AuthError):
        await client.get("/v1/users/42")
    await client.aclose()


async def test_403_raises_forbidden_error() -> None:
    client = api(lambda _: httpx.Response(403))

    with pytest.raises(ForbiddenError):
        await client.get("/v1/users/42")
    await client.aclose()


@pytest.mark.parametrize("status", [400, 422, 418])
async def test_other_4xx_raises_invalid_request(status: int) -> None:
    client = api(lambda _: httpx.Response(status, json={"detail": "bad"}))

    with pytest.raises(InvalidRequestError) as err:
        await client.post("/v1/orders", json={}, idempotency_key="k")
    await client.aclose()

    assert err.value.status_code == status
    assert "bad" in err.value.body_snippet


async def test_429_raises_rate_limited_with_retry_after() -> None:
    client = api(lambda _: httpx.Response(429, headers={"Retry-After": "7"}))

    with pytest.raises(RateLimitedError) as err:
        await client.get("/v1/users/42")
    await client.aclose()

    assert isinstance(err.value, UnavailableError)
    assert err.value.retry_after == 7.0


async def test_retry_after_http_date_is_parsed() -> None:
    client = api(
        lambda _: httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})
    )

    with pytest.raises(RateLimitedError) as err:
        await client.get("/v1/users/42")
    await client.aclose()

    assert err.value.retry_after == 0.0


async def test_5xx_after_retries_raises_unavailable() -> None:
    client = api(lambda _: httpx.Response(503), max_retries=1)

    with pytest.raises(UnavailableError) as err:
        await client.get("/v1/users/42")
    await client.aclose()

    assert err.value.status_code == 503


async def test_transport_error_raises_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = api(handler)

    with pytest.raises(UnavailableError) as err:
        await client.get("/v1/users/42")
    await client.aclose()

    assert isinstance(err.value.__cause__, httpx.ConnectError)


async def test_cross_host_redirect_raises_unavailable() -> None:
    client = api(lambda _: httpx.Response(302, headers={"Location": "http://attacker/x"}))

    with pytest.raises(UnavailableError) as err:
        await client.get("/v1/users/42")
    await client.aclose()

    assert isinstance(err.value.__cause__, CrossHostRedirectError)


async def test_breaker_open_raises_unavailable() -> None:
    client = api(lambda _: httpx.Response(503), breaker={"failure_threshold": 1})

    with pytest.raises(UnavailableError):
        await client.get("/v1/users/42")
    with pytest.raises(UnavailableError) as err:
        await client.get("/v1/users/42")
    await client.aclose()

    assert isinstance(err.value.__cause__, BreakerOpenError)


async def test_mutation_without_key_is_single_attempt() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    client = api(handler, max_retries=2)

    with pytest.raises(UnavailableError):
        await client.post("/v1/orders", json={})
    await client.aclose()

    assert calls == 1


async def test_mutation_with_key_uses_retry_budget() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    client = api(handler, max_retries=2)

    with pytest.raises(UnavailableError):
        await client.post("/v1/orders", json={}, idempotency_key="draft-1")
    await client.aclose()

    assert calls == 3


async def test_get_always_uses_retry_budget() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    client = api(handler, max_retries=2)

    with pytest.raises(UnavailableError):
        await client.get("/v1/users/42")
    await client.aclose()

    assert calls == 3


async def test_idempotency_key_header() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("Idempotency-Key")
        return httpx.Response(200, json={})

    client = api(handler)
    await client.post("/v1/orders", json={}, idempotency_key="draft-1")
    await client.aclose()

    assert seen["key"] == "draft-1"


async def test_no_idempotency_key_by_default() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("Idempotency-Key")
        return httpx.Response(200, json={})

    client = api(handler)
    await client.post("/v1/orders", json={})
    await client.aclose()

    assert seen["key"] is None


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
async def test_all_verbs_reach_the_transport(method: str) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        return httpx.Response(200, json={"ok": True})

    client = api(handler)
    call = getattr(client, method.lower())
    if method == "DELETE":
        await call("/v1/orders/1")
    else:
        await call("/v1/orders/1", json={})
    await client.aclose()

    assert seen["method"] == method


async def test_request_forwards_arbitrary_method() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"method": request.method})

    client = api(handler)

    assert await client.request("OPTIONS", "/v1/orders") == {"method": "OPTIONS"}
    await client.aclose()


async def test_query_params_forwarded() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["q"] = str(request.url.params)
        return httpx.Response(200, json={})

    client = api(handler)
    await client.get("/v1/users", params={"page": 2})
    await client.aclose()

    assert seen["q"] == "page=2"


async def test_api_client_exposes_underlying_client() -> None:
    client = api(lambda _: httpx.Response(200, json={}))

    assert isinstance(client.client, Client)
    await client.aclose()


def test_unknown_backend_names_the_known_ones() -> None:
    clients = ApiClients({"crm": api(lambda _: httpx.Response(200))})

    assert clients.names() == ("crm",)
    assert "crm" in clients
    with pytest.raises(UnknownBackendError, match="known: crm"):
        clients.get("nope")


async def test_error_text_keeps_query_and_userinfo_out() -> None:
    client = api(lambda _: httpx.Response(404), base_url="")

    with pytest.raises(NotFoundError) as err:
        await client.get("https://u:p4ss@api.example/v1/users", params={"api_key": "T0KEN"})
    await client.aclose()

    message = str(err.value)
    assert "T0KEN" not in message
    assert "p4ss" not in message
    assert message == "GET https://api.example/v1/users: 404"


def test_body_snippet_is_best_effort() -> None:
    from pylibinfra.httpclient.api import _snippet

    unread = httpx.Response(500, stream=httpx.AsyncByteStream())

    assert _snippet(unread) == ""


async def test_attempt_timeout_raises_unavailable() -> None:
    async def handler(_: Received, writer: asyncio.StreamWriter) -> None:
        await asyncio.sleep(5)
        await respond(writer, 200)

    async with serve(handler) as server:
        config = cfg(timeout=0.05, max_retries=0, breaker={"failure_threshold": 0})
        client = ApiClient(Client(config))
        with pytest.raises(UnavailableError) as err:
            await client.get(f"{server.url}/v1/users/42")
        await client.aclose()

    assert isinstance(err.value.__cause__, TimeoutError)
