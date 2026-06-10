import httpx
import pytest
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.httpclient import (
    ApiClient,
    ApiClients,
    ApiClientsProvider,
    BackendsConfig,
    BreakerConfig,
    BreakerOpenError,
    Client,
    Config,
    ConflictError,
    NotFoundError,
    UnavailableError,
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
    assert await api.post("/v1/orders/1/fulfill") is None
    await api.aclose()


async def test_2xx_non_json_body_raises_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>WAF</html>")

    api = _api(handler)
    with pytest.raises(UnavailableError, match="non-JSON"):
        await api.get("/v1/users/42")
    await api.aclose()


async def test_404_raises_not_found() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    api = _api(handler)
    with pytest.raises(NotFoundError):
        await api.get("/v1/users/404")
    await api.aclose()


async def test_409_raises_conflict() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(409)

    api = _api(handler)
    with pytest.raises(ConflictError):
        await api.post("/v1/orders", json={})
    await api.aclose()


async def test_other_4xx_raises_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(422)

    api = _api(handler)
    with pytest.raises(UnavailableError):
        await api.post("/v1/orders", json={})
    await api.aclose()


async def test_5xx_after_retries_raises_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    api = _api(handler, _cfg(HTTPCLIENT_MAX_RETRIES="1"))
    with pytest.raises(UnavailableError):
        await api.get("/v1/users/42")
    await api.aclose()


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
                "crm": _cfg(HTTPCLIENT_BASE_URL="http://crm"),
                "billing": _cfg(
                    HTTPCLIENT_BASE_URL="http://billing",
                    breaker=BreakerConfig(name="custom"),
                ),
            }
        )


async def test_named_clients_resolve_and_unknown_raises() -> None:
    container = make_async_container(ApiClientsProvider(), _BackendsCfg())
    apis = await container.get(ApiClients)

    assert isinstance(apis.get("crm"), ApiClient)
    with pytest.raises(KeyError, match="is not configured"):
        apis.get("nope")
    await container.close()


async def test_named_clients_breaker_names() -> None:
    container = make_async_container(ApiClientsProvider(), _BackendsCfg())
    apis = await container.get(ApiClients)

    # дефолтное имя breaker'а заменяется именем бэкенда, явное — сохраняется
    assert apis.get("crm")._client._cfg.breaker.name == "crm"
    assert apis.get("billing")._client._cfg.breaker.name == "custom"
    await container.close()


async def test_backends_config_from_env_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPCLIENT_BACKENDS", '{"crm": {"base_url": "http://crm"}}')

    cfg = BackendsConfig()

    assert cfg.backends["crm"].base_url == "http://crm"
