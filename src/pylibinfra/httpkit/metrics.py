"""RED-метрики кита и label'ы `path`/`method` с конечной кардинальностью."""

from __future__ import annotations

import time

from opentelemetry.metrics import Counter, Histogram, Meter
from opentelemetry.trace import get_current_span
from starlette.types import ASGIApp, Receive, Scope, Send

from pylibinfra._internal import otelkit, requestid

from .response import tap

KIT = "httpkit"
UNMATCHED = "unmatched"

_KNOWN_METHODS = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "CONNECT", "TRACE"}
)


class Instruments:
    """Инструменты httpkit на одном Meter; создаются один раз на приложение."""

    __slots__ = (
        "idempotency",
        "max_body_rejections",
        "panics_total",
        "rate_limited",
        "req_duration",
        "req_total",
    )

    def __init__(self, meter: Meter) -> None:
        """Регистрирует счётчики и гистограмму длительности с общими бакетами китов."""
        self.req_total: Counter = meter.create_counter(
            "http_requests_total",
            description="HTTP requests by method, route pattern and status",
        )
        self.req_duration: Histogram = otelkit.duration_histogram(
            meter, "http_request_duration_seconds", "HTTP request duration"
        )
        self.panics_total: Counter = meter.create_counter(
            "http_panics_total",
            description="Unhandled exceptions recovered in the HTTP handler chain",
        )
        self.max_body_rejections: Counter = meter.create_counter(
            "http_max_body_rejections_total",
            description="Requests rejected because the body exceeded MaxBytes",
        )
        self.idempotency: Counter = meter.create_counter(
            "http_idempotency_total",
            description="Idempotency middleware outcomes",
        )
        self.rate_limited: Counter = meter.create_counter(
            "http_rate_limited_total",
            description="Requests rejected by the per-client rate limiter",
        )


class _Cache:
    """Кэш инструментов на Meter: повторная регистрация того же имени шумит в OTel-SDK."""

    meter: Meter | None = None
    value: Instruments | None = None


def instruments() -> Instruments:
    """Инструменты текущего глобального Meter; после смены MeterProvider пересоздаются."""
    meter = otelkit.meter(KIT)
    if _Cache.value is None or _Cache.meter is not meter:
        _Cache.meter, _Cache.value = meter, Instruments(meter)
    return _Cache.value


def method_label(method: str) -> str:
    """Метод из конечного множества; произвольный токен клиента становится `other`."""
    return method if method in _KNOWN_METHODS else "other"


def route_label(scope: Scope) -> str:
    """Шаблон маршрута после роутинга; `unmatched` — если маршрут не подобрался."""
    route = scope.get("route")
    pattern = getattr(route, "path_format", None) or getattr(route, "path", None)
    return pattern if isinstance(pattern, str) and pattern else UNMATCHED


class MetricsMiddleware:
    """Эмитит `http_requests_total` и `http_request_duration_seconds`, дополняет server-спан."""

    def __init__(self, app: ASGIApp, instr: Instruments | None = None) -> None:
        """`instr` по умолчанию — общий набор текущего Meter."""
        self._app = app
        self._instr = instr if instr is not None else instruments()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Пишет сэмпл даже когда снизу прилетело исключение."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        started = time.perf_counter()
        observed = tap(scope, send)
        try:
            await self._app(scope, receive, observed.send)
        finally:
            route = route_label(scope)
            self._annotate_span(route)
            attrs = {
                "method": method_label(str(scope.get("method", ""))),
                "path": route,
                "status": str(observed.status),
            }
            self._instr.req_total.add(1, attrs)
            self._instr.req_duration.record(time.perf_counter() - started, attrs)

    def _annotate_span(self, route: str) -> None:
        span = get_current_span()
        if not span.is_recording():
            return
        span.set_attribute("http.route", route)
        rid = requestid.current()
        if rid:
            span.set_attribute("request_id", rid)
