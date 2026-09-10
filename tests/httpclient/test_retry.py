from __future__ import annotations

import asyncio
import ssl
from datetime import UTC, datetime

import httpx
import pytest

from pylibinfra.httpclient import (
    Client,
    Config,
    allow_retry,
    parse_retry_after,
)

from .conftest import Received, cfg, mock_client, respond, serve


def counting(*statuses: int) -> tuple[list[httpx.Request], object]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        index = min(len(seen) - 1, len(statuses) - 1)
        return httpx.Response(statuses[index])

    return seen, handler


async def test_success_is_not_retried() -> None:
    seen, handler = counting(200)

    async with mock_client(handler) as client:  # type: ignore[arg-type]
        response = await client.get("/ping")

    assert response.status_code == 200
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("status", "calls"),
    [(408, 3), (429, 3), (502, 3), (503, 3), (504, 3), (500, 1), (501, 1), (400, 1), (404, 1)],
)
async def test_only_transient_statuses_are_retried(status: int, calls: int) -> None:
    seen, handler = counting(status)
    config = cfg(max_retries=2, breaker={"failure_threshold": 0})

    async with mock_client(handler, config) as client:  # type: ignore[arg-type]
        response = await client.get("/ping")

    assert response.status_code == status
    assert len(seen) == calls


async def test_exhausted_retries_return_last_response() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="upstream down")

    config = cfg(max_retries=2, breaker={"failure_threshold": 0})
    async with mock_client(handler, config) as client:
        response = await client.get("/ping")

    # 5xx после ретраев — это ответ, а не исключение; тело читаемо.
    assert response.status_code == 502
    assert response.text == "upstream down"


async def test_post_with_body_is_not_retried_by_default() -> None:
    seen, handler = counting(503)
    config = cfg(breaker={"failure_threshold": 0})

    async with mock_client(handler, config) as client:  # type: ignore[arg-type]
        await client.post("/orders", json={"a": 1})

    assert len(seen) == 1


async def test_allow_retry_enables_post_and_body_survives() -> None:
    seen, handler = counting(503, 503, 201)
    config = cfg(breaker={"failure_threshold": 0})

    async with mock_client(handler, config) as client:  # type: ignore[arg-type]
        with allow_retry():
            response = await client.post("/orders", json={"a": 1})

    assert response.status_code == 201
    assert len(seen) == 3
    assert [bytes(r.content) for r in seen] == [b'{"a":1}'] * 3


async def test_idempotency_key_header_enables_retry() -> None:
    seen, handler = counting(502, 200)
    config = cfg(breaker={"failure_threshold": 0})

    async with mock_client(handler, config) as client:  # type: ignore[arg-type]
        await client.post("/orders", json={}, headers={"Idempotency-Key": "k1"})

    assert len(seen) == 2


async def test_retry_non_idempotent_flag_enables_retry() -> None:
    seen, handler = counting(502, 200)
    config = cfg(retry_non_idempotent=True, breaker={"failure_threshold": 0})

    async with mock_client(handler, config) as client:  # type: ignore[arg-type]
        await client.post("/orders", json={})

    assert len(seen) == 2


async def test_streaming_body_is_not_retried() -> None:
    seen, handler = counting(503)
    config = cfg(retry_non_idempotent=True, breaker={"failure_threshold": 0})

    async def body() -> object:
        yield b"stream"

    async with mock_client(handler, config) as client:  # type: ignore[arg-type]
        await client.put("/blob", content=body())

    assert len(seen) == 1


async def test_multipart_body_is_not_retried() -> None:
    seen, handler = counting(503)
    config = cfg(retry_non_idempotent=True, breaker={"failure_threshold": 0})

    # multipart остаётся потоком: повтор потребовал бы буферизовать загрузку целиком
    async with mock_client(handler, config) as client:  # type: ignore[arg-type]
        await client.put("/blob", files={"f": ("n.txt", b"payload")})

    assert len(seen) == 1


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "PUT", "DELETE", "TRACE"])
async def test_idempotent_methods_are_retried(method: str) -> None:
    seen, handler = counting(503, 200)
    config = cfg(breaker={"failure_threshold": 0})

    async with mock_client(handler, config) as client:  # type: ignore[arg-type]
        await client.request(method, "/x")

    assert len(seen) == 2


async def test_transport_error_is_retried() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ConnectError("connection reset by peer")
        return httpx.Response(200)

    async with mock_client(handler, cfg(breaker={"failure_threshold": 0})) as client:
        response = await client.get("/ping")

    assert response.status_code == 200
    assert calls == 3


async def test_transport_error_after_retries_raises() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    config = cfg(max_retries=1, breaker={"failure_threshold": 0})
    async with mock_client(handler, config) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("/ping")


async def test_tls_error_is_permanent() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("bad cert") from ssl.SSLCertVerificationError("unknown authority")

    async with mock_client(handler, cfg(breaker={"failure_threshold": 0})) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("/ping")

    assert calls == 1


async def test_unsupported_scheme_is_permanent() -> None:
    calls = 0

    async def hook(_: int) -> None:
        nonlocal calls
        calls += 1

    config = cfg(base_url="", breaker={"failure_threshold": 0})
    async with Client(config, before_attempt=hook) as client:
        with pytest.raises(httpx.UnsupportedProtocol):
            await client.get("ftp://upstream.invalid/x")

    assert calls == 1


async def test_attempt_timeout_is_retried() -> None:
    async def handler(request: Received, writer: asyncio.StreamWriter) -> None:
        if request.target == "/slow":
            await asyncio.sleep(5)
        await respond(writer, 200)

    async with serve(handler) as server:
        config = cfg(timeout=0.05, max_retries=1, breaker={"failure_threshold": 0})
        async with Client(config) as client:
            with pytest.raises(TimeoutError):
                await client.get(f"{server.url}/slow")
            response = await client.get(f"{server.url}/fast")

    assert response.status_code == 200
    assert server.count() == 3


async def test_caller_cancellation_is_not_retried() -> None:
    calls = 0

    async def handler(_: Received, writer: asyncio.StreamWriter) -> None:
        nonlocal calls
        calls += 1
        await asyncio.sleep(5)
        await respond(writer, 200)

    async with serve(handler) as server, Client(cfg(breaker={"failure_threshold": 0})) as client:
        task = asyncio.create_task(client.get(f"{server.url}/slow"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert calls == 1


async def test_cancellation_during_backoff_stops_retry() -> None:
    calls, handler = counting(503)
    config = cfg(retry_wait=5.0, retry_max_wait=5.0, breaker={"failure_threshold": 0})

    async with mock_client(handler, config) as client:  # type: ignore[arg-type]
        task = asyncio.create_task(client.get("/ping"))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(calls) == 1


async def test_before_attempt_runs_on_every_attempt() -> None:
    attempts: list[int] = []

    async def hook(attempt: int) -> None:
        attempts.append(attempt)

    _, handler = counting(502, 502, 200)
    config = cfg(breaker={"failure_threshold": 0})

    async with mock_client(handler, config, before_attempt=hook) as client:  # type: ignore[arg-type]
        await client.get("/ping")

    assert attempts == [0, 1, 2]


async def test_before_attempt_error_aborts_without_request() -> None:
    sent, handler = counting(200)
    limited = RuntimeError("rate limited")

    async def hook(_: int) -> None:
        raise limited

    async with mock_client(handler, cfg(), before_attempt=hook) as client:  # type: ignore[arg-type]
        with pytest.raises(RuntimeError, match="rate limited"):
            await client.get("/ping")

    assert sent == []


async def test_retry_after_beats_long_backoff() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200)

    config = cfg(
        max_retries=1, retry_wait=60.0, retry_max_wait=60.0, breaker={"failure_threshold": 0}
    )
    async with mock_client(handler, config) as client:
        async with asyncio.timeout(5):
            response = await client.get("/ping")

    assert response.status_code == 200
    assert len(seen) == 2


def test_backoff_stays_within_bounds() -> None:
    client = Client(cfg(retry_wait=0.1, retry_max_wait=0.25))

    for attempt in range(1, 41):
        assert 0.1 <= client._backoff(attempt, None) <= 0.25


def test_backoff_does_not_overflow_on_large_base() -> None:
    client = Client(cfg(retry_wait=20.0, retry_max_wait=60.0))

    for attempt in (1, 2, 30, 40, 62, 63, 100):
        assert 0 < client._backoff(attempt, None) <= 60.0


def test_retry_after_is_capped_by_max_wait() -> None:
    client = Client(cfg(retry_wait=0.001, retry_max_wait=2.0))
    response = httpx.Response(429, headers={"Retry-After": "600"})
    absurd = httpx.Response(429, headers={"Retry-After": "9223372037"})

    assert client._backoff(1, response) == 2.0
    assert client._backoff(1, absurd) == 2.0


def test_retry_after_is_capped_by_five_minutes() -> None:
    client = Client(cfg(retry_wait=0.001, retry_max_wait=3600.0))
    response = httpx.Response(429, headers={"Retry-After": "600"})

    assert client._backoff(1, response) == 300.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", None),
        ("3", 3.0),
        ("0", 0.0),
        ("1.5", 2.0),
        ("-5", None),
        ("soon", None),
        ("Inf", None),
        ("NaN", None),
        ("1e999", None),
        ("1_0", None),
        ("Thu, 14 Nov 2023 22:15:30 GMT", 30.0),
        ("Thu, 14 Nov 2023 21:15:30 GMT", 0.0),
    ],
)
def test_parse_retry_after(value: str, expected: float | None) -> None:
    now = datetime(2023, 11, 14, 22, 15, 0, tzinfo=UTC)

    assert parse_retry_after(value, now) == expected


def test_parse_retry_after_saturates_absurd_seconds() -> None:
    # В Go значение насыщалось максимумом time.Duration; во float потолок ставит
    # вызывающий, а разбор возвращает положительное число, а не отрицательную паузу.
    parsed = parse_retry_after("184467440737095516160", datetime.now(UTC))

    assert parsed is not None
    assert parsed > 0


def test_config_property_exposes_effective_policy() -> None:
    config = cfg(max_retries=1)

    assert Client(config).config.max_retries == 1


def test_new_builds_client_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from pylibinfra.httpclient import new

    monkeypatch.setenv("HTTPCLIENT_TIMEOUT", "3")

    assert new().config.timeout == 3.0
    assert new(Config(timeout=7)).config.timeout == 7.0


@pytest.mark.parametrize("method", ["PATCH", "DELETE", "PUT", "POST"])
async def test_convenience_verbs_reach_the_transport(method: str) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        return httpx.Response(200)

    async with mock_client(handler, cfg(max_retries=0)) as client:
        await getattr(client, method.lower())("/x")

    assert seen == [method]


@pytest.mark.parametrize("value", ["1.2.3", "-", "+", "e"])
def test_parse_retry_after_rejects_malformed_numbers(value: str) -> None:
    assert parse_retry_after(value, datetime.now(UTC)) is None


def test_parse_retry_after_treats_naive_moments_as_utc() -> None:
    now = datetime(2023, 11, 14, 22, 15, 0)

    assert parse_retry_after("Tue, 14 Nov 2023 22:16:00 -0000", now) == 60.0
