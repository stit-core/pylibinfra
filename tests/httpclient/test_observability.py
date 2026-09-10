from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from pylibinfra._internal import requestid
from pylibinfra.httpclient import BreakerOpenError

from .conftest import cfg, mock_client


def duration_attributes(reader: InMemoryMetricReader) -> list[dict[str, Any]]:
    data = reader.get_metrics_data()
    points: list[dict[str, Any]] = []
    if data is None:
        return points
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name != "http_client_request_duration_seconds":
                    continue
                points += [dict(point.attributes or {}) for point in metric.data.data_points]
    return points


async def test_duration_labels_cover_status_method_and_host(
    metric_reader: InMemoryMetricReader,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if request.url.path == "/ok" else 503)

    config = cfg(max_retries=1, base_url="http://crm", breaker={"failure_threshold": 1})
    async with mock_client(handler, config) as client:
        await client.get("/ok")
        await client.get("/down")
        with pytest.raises(BreakerOpenError):
            await client.get("/down")

    statuses = {point["status"] for point in duration_attributes(metric_reader)}
    assert statuses == {"200", "503", "breaker_open"}
    assert {point["host"] for point in duration_attributes(metric_reader)} == {"crm"}
    assert {point["method"] for point in duration_attributes(metric_reader)} == {"GET"}


async def test_plain_calls_have_no_mode_label(metric_reader: InMemoryMetricReader) -> None:
    async with mock_client(lambda _: httpx.Response(200)) as client:
        await client.get("/ok")

    assert all("mode" not in point for point in duration_attributes(metric_reader))


async def test_transport_error_is_labelled_error(metric_reader: InMemoryMetricReader) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    config = cfg(max_retries=0, breaker={"failure_threshold": 0})
    async with mock_client(handler, config) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("/ping")

    assert {point["status"] for point in duration_attributes(metric_reader)} == {"error"}


async def test_retry_attempts_counted(read_metric: Callable[..., float]) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503) if calls < 3 else httpx.Response(200)

    config = cfg(max_retries=3, base_url="http://crm", breaker={"failure_threshold": 0})
    async with mock_client(handler, config) as client:
        assert (await client.get("/ping")).status_code == 200

    # 3 запроса на транспорт — 2 повтора сверх первой попытки.
    assert read_metric("http_client_retry_attempts_total", {"host": "crm"}) == 2


async def test_call_span_carries_semconv_attributes(span_exporter: InMemorySpanExporter) -> None:
    async with mock_client(lambda _: httpx.Response(204)) as client:
        await client.get("/items", params={"token": "secret"})

    span = next(s for s in span_exporter.get_finished_spans() if s.name == "httpclient.Do GET")
    assert span.attributes is not None
    assert span.attributes["http.request.method"] == "GET"
    assert span.attributes["server.address"] == "svc"
    # url.full без query: секрет не должен уезжать в трейс.
    assert span.attributes["url.full"] == "http://svc/items"
    assert span.attributes["http.response.status_code"] == 204


async def test_error_status_marks_the_span(span_exporter: InMemorySpanExporter) -> None:
    config = cfg(max_retries=0, breaker={"failure_threshold": 0})
    async with mock_client(lambda _: httpx.Response(404), config) as client:
        await client.get("/missing")

    span = next(s for s in span_exporter.get_finished_spans() if s.name == "httpclient.Do GET")
    assert span.status.is_ok is False


async def test_request_id_forwarded_from_context() -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("X-Request-ID"))
        return httpx.Response(200)

    async with mock_client(handler) as client:
        await client.get("/ping")

        token = requestid.bind("from-ctx")
        try:
            await client.get("/ping")
            await client.get("/ping", headers={"X-Request-ID": "explicit-id"})
        finally:
            requestid.unbind(token)

    assert seen == [None, "from-ctx", "explicit-id"]


async def test_attempt_spans_nest_under_the_call_span(
    span_exporter: InMemorySpanExporter,
) -> None:
    config = cfg(max_retries=0, breaker={"failure_threshold": 0})
    async with mock_client(lambda _: httpx.Response(200), config) as client:
        await client.get("/ping")

    spans = span_exporter.get_finished_spans()
    call = next(s for s in spans if s.name == "httpclient.Do GET")
    attempts = [s for s in spans if s is not call]
    assert len(attempts) == 1
    assert attempts[0].parent is not None
    assert attempts[0].parent.span_id == call.context.span_id


async def test_foreign_httpx_client_is_not_instrumented(
    span_exporter: InMemorySpanExporter,
) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200))
    config = cfg(max_retries=0, breaker={"failure_threshold": 0})

    async with mock_client(lambda _: httpx.Response(200), config) as client:
        await client.get("/ping")
    # Кит инструментирует свой httpx-клиент, а не httpx процесса.
    async with httpx.AsyncClient(transport=transport) as foreign:
        await foreign.get("http://elsewhere/ping")

    spans = span_exporter.get_finished_spans()
    assert len([s for s in spans if s.name == "httpclient.Do GET"]) == 1
    assert len([s for s in spans if s.name != "httpclient.Do GET"]) == 1


async def test_aclose_removes_instrumentation() -> None:
    from pylibinfra.httpclient import Client

    client = Client(cfg())
    inner = client._http
    assert inner._is_instrumented_by_opentelemetry is True
    assert hasattr(inner._transport.handle_async_request, "__wrapped__")

    await client.aclose()

    assert inner._is_instrumented_by_opentelemetry is False
    assert not hasattr(inner._transport.handle_async_request, "__wrapped__")
