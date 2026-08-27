import httpx
import pytest
from dishka import Provider, Scope, make_async_container, provide
from pydantic import BaseModel

from pylibinfra.httpclient import (
    ApiClient,
    ApiClients,
    ApiClientsProvider,
    AuthError,
    BackendConfig,
    BackendsConfig,
    BreakerConfig,
    BreakerOpenError,
    Client,
    Config,
    ConflictError,
    ContractError,
    ForbiddenError,
    InvalidRequestError,
    NotFoundError,
    RateLimitedError,
    UnavailableError,
    UnknownBackendError,
)


def _cfg(**over: object) -> Config:
    base: dict[str, object] = {
        "HTTPCLIENT_TIMEOUT": "5.0",
        "HTTPCLIENT_MAX_RETRIES": "0",
        "HTTPCLIENT_RETRY_WAIT": "0.0",
    }
    base.update(over)
    return Config(**base)  # type: ignore[arg-type]


def _api(handler: object, cfg: Config | None = None) -> ApiClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    http = httpx.AsyncClient(transport=transport, base_url="http://svc")
    return ApiClient(Client(cfg or _cfg(), http))


async def test_2xx_json_body() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "42", "name": "n"})

    api = _api(handler)
    body = await api.get("/v1/users/42")
    await api.aclose()

    assert body == {"id": "42", "name": "n"}


async def test_2xx_empty_body_is_none() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    api = _api(handler)
    assert await api.post("/v1/orders/1/fulfill", idempotency_key="k") is None
    await api.aclose()


async def test_2xx_non_json_body_raises_contract_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>WAF</html>")

    api = _api(handler)
    with pytest.raises(ContractError, match="non-JSON") as err:
        await api.get("/v1/users/42")
    await api.aclose()

    # ContractError — подкласс UnavailableError: catch-сайты «бэкенд недоступен» его ловят
    assert isinstance(err.value, UnavailableError)
    assert err.value.body_snippet.startswith("<html>")


class _User(BaseModel):
    id: str
    name: str


async def test_model_decodes_into_wire_model() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "42", "name": "n"})

    api = _api(handler)
    user = await api.get("/v1/users/42", model=_User)
    await api.aclose()

    assert user == _User(id="42", name="n")


async def test_model_mismatch_raises_contract_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "42"})

    api = _api(handler)
    with pytest.raises(ContractError, match="_User") as err:
        await api.get("/v1/users/42", model=_User)
    await api.aclose()

    assert err.value.__cause__ is not None


async def test_404_raises_not_found_with_context() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    api = _api(handler)
    with pytest.raises(NotFoundError) as err:
        await api.get("/v1/users/404")
    await api.aclose()

    assert err.value.status_code == 404
    assert err.value.method == "GET"
    assert err.value.path == "/v1/users/404"


async def test_409_raises_conflict() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(409)

    api = _api(handler)
    with pytest.raises(ConflictError):
        await api.post("/v1/orders", json={}, idempotency_key="k")
    await api.aclose()


async def test_401_raises_auth_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    api = _api(handler)
    with pytest.raises(AuthError):
        await api.get("/v1/users/42")
    await api.aclose()


async def test_403_raises_forbidden_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    api = _api(handler)
    with pytest.raises(ForbiddenError):
        await api.get("/v1/users/42")
    await api.aclose()


@pytest.mark.parametrize("status", [400, 422, 418])
async def test_other_4xx_raises_invalid_request(status: int) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": "bad"})

    api = _api(handler)
    with pytest.raises(InvalidRequestError) as err:
        await api.post("/v1/orders", json={}, idempotency_key="k")
    await api.aclose()

    assert err.value.status_code == status
    assert "bad" in err.value.body_snippet


async def test_429_raises_rate_limited_with_retry_after() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "7"})

    api = _api(handler)
    with pytest.raises(RateLimitedError) as err:
        await api.get("/v1/users/42")
    await api.aclose()

    # transient: подкласс UnavailableError, чтобы деградация «позже» срабатывала
    assert isinstance(err.value, UnavailableError)
    assert err.value.retry_after == 7.0


async def test_5xx_after_retries_raises_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    api = _api(handler, _cfg(HTTPCLIENT_MAX_RETRIES="1"))
    with pytest.raises(UnavailableError) as err:
        await api.get("/v1/users/42")
    await api.aclose()

    assert err.value.status_code == 503


async def test_transport_error_raises_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    api = _api(handler)
    with pytest.raises(UnavailableError):
        await api.get("/v1/users/42")
    await api.aclose()


async def test_breaker_open_raises_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    cfg = _cfg()
    cfg = cfg.model_copy(
        update={"breaker": BreakerConfig(failure_threshold=1, recovery_timeout=60.0)}
    )
    api = _api(handler, cfg)
    with pytest.raises(UnavailableError):
        await api.get("/v1/users/42")
    with pytest.raises(UnavailableError) as err:
        await api.get("/v1/users/42")
    await api.aclose()

    assert isinstance(err.value.__cause__, BreakerOpenError)


async def test_mutation_without_key_is_single_attempt() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    api = _api(handler, _cfg(HTTPCLIENT_MAX_RETRIES="2"))
    with pytest.raises(UnavailableError):
        await api.post("/v1/orders", json={})
    await api.aclose()

    assert calls == 1


async def test_mutation_with_key_uses_retry_budget() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    api = _api(handler, _cfg(HTTPCLIENT_MAX_RETRIES="2"))
    with pytest.raises(UnavailableError):
        await api.post("/v1/orders", json={}, idempotency_key="draft-1")
    await api.aclose()

    assert calls == 3


async def test_get_always_uses_retry_budget() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    api = _api(handler, _cfg(HTTPCLIENT_MAX_RETRIES="2"))
    with pytest.raises(UnavailableError):
        await api.get("/v1/users/42")
    await api.aclose()

    assert calls == 3


async def test_idempotency_key_header() -> None:
    seen: dict[str, str | None] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["key"] = req.headers.get("Idempotency-Key")
        return httpx.Response(200, json={})

    api = _api(handler)
    await api.post("/v1/orders", json={}, idempotency_key="draft-1")
    await api.aclose()

    assert seen["key"] == "draft-1"


async def test_no_idempotency_key_by_default() -> None:
    seen: dict[str, str | None] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["key"] = req.headers.get("Idempotency-Key")
        return httpx.Response(200, json={})

    api = _api(handler)
    await api.post("/v1/orders", json={})
    await api.aclose()

    assert seen["key"] is None


async def test_query_params_forwarded() -> None:
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["q"] = str(req.url.params)
        return httpx.Response(200, json={})

    api = _api(handler)
    await api.get("/v1/users", params={"page": 2})
    await api.aclose()

    assert seen["q"] == "page=2"


class _BackendsCfg(Provider):
    scope = Scope.APP

    @provide
    def cfg(self) -> BackendsConfig:
        return BackendsConfig(
            backends={
                "crm": BackendConfig(base_url="http://crm"),
                "billing": BackendConfig(
                    base_url="http://billing",
                    headers={"Authorization": "Bearer tok"},
                ),
            }
        )


async def test_named_clients_resolve_and_unknown_raises() -> None:
    container = make_async_container(ApiClientsProvider(), _BackendsCfg())
    apis = await container.get(ApiClients)

    assert isinstance(apis.get("crm"), ApiClient)
    assert apis.names() == ("billing", "crm")
    assert "crm" in apis
    with pytest.raises(UnknownBackendError, match="is not configured"):
        apis.get("nope")
    await container.close()


async def test_named_clients_breaker_named_by_backend_key() -> None:
    container = make_async_container(ApiClientsProvider(), _BackendsCfg())
    apis = await container.get(ApiClients)

    # имя breaker'а — всегда ключ бэкенда: per-backend ряды метрик без коллизий
    assert apis.get("crm")._client._cfg.breaker.name == "crm"
    assert apis.get("billing")._client._cfg.breaker.name == "billing"
    await container.close()


async def test_named_clients_carry_declared_headers() -> None:
    container = make_async_container(ApiClientsProvider(), _BackendsCfg())
    apis = await container.get(ApiClients)

    assert apis.get("billing")._client._http.headers["Authorization"] == "Bearer tok"
    await container.close()


async def test_backends_config_from_env_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPCLIENT_BACKENDS", '{"crm": {"base_url": "http://crm"}}')

    cfg = BackendsConfig()

    assert cfg.backends["crm"].base_url == "http://crm"
