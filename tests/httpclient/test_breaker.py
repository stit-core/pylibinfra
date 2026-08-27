from typing import Any

import httpx
import pytest

from pylibinfra.httpclient import BreakerConfig, BreakerOpenError, Client, Config


def _cfg(**over: object) -> Config:
    base: dict[str, object] = {
        "HTTPCLIENT_TIMEOUT": "5.0",
        "HTTPCLIENT_MAX_RETRIES": "0",
        "HTTPCLIENT_RETRY_WAIT": "0.0",
    }
    base.update(over)
    return Config(**base)  # type: ignore[arg-type]


def _client(handler: object, cfg: Config) -> Client:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    http = httpx.AsyncClient(transport=transport, base_url="http://svc")
    return Client(cfg, http)


async def test_breaker_opens_after_threshold(metrics_view: Any) -> None:
    threshold = 5
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    cfg = _cfg(HTTPCLIENT_BREAKER_FAILURE_THRESHOLD=str(threshold))
    c = _client(handler, cfg)

    for _ in range(threshold):
        with pytest.raises(httpx.HTTPStatusError):
            await c.get("http://svc/ping")

    calls_before = calls
    # Брейкер открыт: следующий вызов фейлится быстро без обращения к transport.
    with pytest.raises(BreakerOpenError):
        await c.get("http://svc/ping")
    await c.aclose()

    assert calls == calls_before
    assert metrics_view.total("http_client_breaker_open") == 1
    assert metrics_view.total("http_client_breaker_transitions_total") >= 1


async def test_breaker_name_labels_metrics(metrics_view: Any) -> None:
    # Имя брейкера задаётся только кодом (у именованных бэкендов — ApiClientsProvider);
    # env-ручки нет намеренно: глобальная схлопывала бы per-backend ряды метрик.
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    cfg = _cfg().model_copy(update={"breaker": BreakerConfig(failure_threshold=2, name="crm")})
    c = _client(handler, cfg)
    for _ in range(2):
        with pytest.raises(httpx.HTTPStatusError):
            await c.get("http://svc/ping")
    with pytest.raises(BreakerOpenError):
        await c.get("http://svc/ping")
    await c.aclose()

    # breaker_name → label {"name": "crm"} (per-API метрики), не дефолт "httpclient".
    assert metrics_view.total("http_client_breaker_open", {"name": "crm"}) == 1
    assert metrics_view.total("http_client_breaker_open", {"name": "httpclient"}) == 0


async def test_breaker_stays_closed_on_success() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    c = _client(handler, _cfg(HTTPCLIENT_BREAKER_FAILURE_THRESHOLD="2"))
    for _ in range(5):
        resp = await c.get("http://svc/ping")
        assert resp.status_code == 200
    await c.aclose()


async def test_retry_attempts_counted(metrics_view: Any) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503) if calls < 3 else httpx.Response(200)

    c = _client(handler, _cfg(HTTPCLIENT_MAX_RETRIES="3"))
    resp = await c.get("http://svc/ping")
    await c.aclose()

    assert resp.status_code == 200
    # 3 transport-вызова → 2 ретрая (попытки сверх первой).
    assert metrics_view.total("http_client_retry_attempts_total") == 2
