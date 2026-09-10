from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from dishka import Provider, Scope, make_async_container, provide
from opentelemetry import trace

from pylibinfra import logkit, telemetry


class Collector:
    """httptest-аналог OTLP-коллектора: запоминает путь и заголовки каждого запроса."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, dict[str, str]]] = []
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                recorder.seen.append((self.path, dict(self.headers)))
                self.send_response(200)
                self.send_header("Content-Type", "application/x-protobuf")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}"

    def __enter__(self) -> Collector:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def paths(self) -> set[str]:
        return {path for path, _ in self.seen}

    def headers(self, path: str) -> dict[str, str]:
        return next(headers for seen, headers in self.seen if seen == path)


@pytest.fixture
def collector() -> Iterator[Collector]:
    with Collector() as recorder:
        yield recorder


class Configs(Provider):
    """App-Provider консьюмера: конфиги регистрирует он, не киты."""

    scope = Scope.APP

    def __init__(self, endpoint: str) -> None:
        super().__init__()
        self._endpoint = endpoint

    @provide
    def telemetry_config(self) -> telemetry.Config:
        return telemetry.Config(
            service_name="svc",
            otlp_endpoint=self._endpoint,
            otlp_headers="x-scope-orgid=acme",
            otlp_bearer_token="tok",
            export_timeout=5,
            trace_batch_delay=0.1,
            metric_interval=60,
            log_batch_delay=0.1,
        )

    @provide
    def log_config(self) -> logkit.Config:
        return logkit.Config(silent=True)


async def test_quick_start_wires_the_container(collector: Collector) -> None:
    container = make_async_container(
        logkit.LogkitProvider(),
        telemetry.TelemetryProvider(),
        Configs(collector.url),
    )

    providers = await telemetry.start(container)
    log = await container.get(logkit.Logger)
    log.info("service started")

    assert providers.logger_provider is await container.get(logkit.OptionalLoggerProvider)
    await container.close()


async def test_all_three_signals_reach_the_collector(collector: Collector) -> None:
    container = make_async_container(
        logkit.LogkitProvider(),
        telemetry.TelemetryProvider(),
        Configs(collector.url),
    )
    providers = await telemetry.start(container)

    with trace.get_tracer("test").start_as_current_span("op"):
        (await container.get(logkit.Logger)).info("exported")
    providers.meter_provider.get_meter("test").create_counter("probe_total").add(1)
    await container.close()

    assert collector.paths() >= {"/v1/traces", "/v1/metrics", "/v1/logs"}
    for path in ("/v1/traces", "/v1/metrics", "/v1/logs"):
        headers = collector.headers(path)
        assert headers["Authorization"] == "Bearer tok"
        assert headers["X-Scope-Orgid"] == "acme"
