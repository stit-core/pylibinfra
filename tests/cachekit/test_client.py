"""Клиент кита: имя команды в label'е, политика повторов, проверка версии, метрики отказа."""

from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import Callable

import pytest
import redis.exceptions
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from pylibinfra.cachekit import Config, ServerVersionError
from pylibinfra.cachekit.client import (
    _retry,
    _timeout,
    check_info_version,
    command_name,
    new_client,
)


def closed_addr() -> str:
    """Гарантированно свободный адрес: connect в него отвергается сразу."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{sock.getsockname()[1]}"


def offline_config() -> Config:
    return Config.model_validate(
        {
            "REDIS_URL": f"redis://{closed_addr()}",
            "REDIS_MAX_RETRIES": -1,
            "REDIS_DIAL_TIMEOUT": 1.0,
            "REDIS_READ_TIMEOUT": 1.0,
        }
    )


@pytest.mark.parametrize(
    ("args", "want"),
    [
        (("GET", "k"), "get"),
        ((b"SET", "k", "v"), "set"),
        (("SCRIPT LOAD", "return 1"), "script"),
        (("EVALSHA", "sha"), "evalsha"),
        ((), "unknown"),
    ],
)
def test_command_name(args: tuple[object, ...], want: str) -> None:
    assert command_name(args) == want


@pytest.mark.parametrize(
    ("max_retries", "want"),
    [(-1, 0), (0, 0), (3, 3), (10, 10)],
)
def test_retry_policy(max_retries: int, want: int) -> None:
    # redis-py читает отрицательное число как «повторять вечно» — Go-семантику
    # «-1 выключает» кит переводит в ноль повторов.
    assert _retry(max_retries).get_retries() == want


@pytest.mark.parametrize(("seconds", "want"), [(0.0, None), (1.5, 1.5)])
def test_timeout_zero_means_unbounded(seconds: float, want: float | None) -> None:
    assert _timeout(seconds) == want


@pytest.mark.parametrize(
    "info",
    [
        {"redis_version": "7.2.4"},
        {"redis_version": "8.0.0"},
        {"redis_version": "7.2.4", "valkey_version": "8.0.1"},
    ],
)
def test_supported_server_versions(info: dict[str, str]) -> None:
    check_info_version(info)


@pytest.mark.parametrize(
    "info",
    [
        {"redis_version": "6.2.24"},
        {"redis_version": "5.0.14"},
        {"os": "Linux"},
        {"redis_version": ""},
        {"redis_version": "experimental"},
    ],
)
def test_unsupported_server_versions(info: dict[str, str]) -> None:
    with pytest.raises(ServerVersionError, match="cachekit: "):
        check_info_version(info)


def test_version_error_points_at_the_escape_hatch() -> None:
    with pytest.raises(ServerVersionError, match="REDIS_SKIP_VERSION_CHECK"):
        check_info_version({"os": "Linux"})


async def test_failed_command_is_counted_and_traced(
    read_metric: Callable[..., float], span_exporter: InMemorySpanExporter
) -> None:
    client = new_client(offline_config())
    try:
        with pytest.raises(redis.exceptions.ConnectionError):
            await client.get("k")
    finally:
        await client.aclose()

    assert read_metric("cache_commands_total", {"cmd": "get", "result": "error"}) == 1
    assert read_metric("cache_command_duration_seconds", {"cmd": "get"}) == 1

    spans = span_exporter.get_finished_spans()
    assert [s.name for s in spans] == ["redis get"]
    assert spans[0].kind is SpanKind.CLIENT
    assert spans[0].attributes["db.system"] == "redis"
    assert spans[0].status.status_code is StatusCode.ERROR
    assert "k" not in dict(spans[0].attributes).values()


async def hold_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Принимает соединение и молчит: так выглядит зависший Redis."""
    try:
        await reader.read()
    finally:
        writer.close()


async def test_caller_deadline_aborts_a_hung_command() -> None:
    # Дедлайн вызывающего доходит до сокета: хендлер с бюджетом 200 ms не ждёт
    # молчащий Redis десять секунд REDIS_READ_TIMEOUT.
    server = await asyncio.start_server(hold_connection, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    cfg = Config.model_validate(
        {
            "REDIS_URL": f"redis://127.0.0.1:{port}",
            "REDIS_MAX_RETRIES": -1,
            "REDIS_READ_TIMEOUT": 10.0,
        }
    )
    client = new_client(cfg)
    started = time.perf_counter()
    try:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.2):
                await client.get("k")
    finally:
        await client.aclose()
        server.close()
        await server.wait_closed()

    assert time.perf_counter() - started < 2.0
