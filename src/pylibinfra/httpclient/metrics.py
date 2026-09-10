"""Сигналы кита: длительность вызова, повторы, состояние цепи и атрибуты спана."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from opentelemetry.metrics import Counter, Histogram, UpDownCounter
from opentelemetry.trace import Span, Status, StatusCode

from pylibinfra._internal import otelkit

KIT = "httpclient"


@dataclass(frozen=True)
class Instruments:
    """Метрики клиента; создаются на объект, чтобы тест видел свой MeterProvider."""

    request_duration: Histogram
    retry_attempts: Counter
    breaker_transitions: Counter
    breaker_open: UpDownCounter


def new_instruments() -> Instruments:
    """Инструменты на глобальном провайдере; без OTel-SDK — no-op."""
    m = otelkit.meter(KIT)
    return Instruments(
        request_duration=otelkit.duration_histogram(
            m,
            "http_client_request_duration_seconds",
            "Outbound HTTP request duration including retries",
        ),
        retry_attempts=m.create_counter(
            "http_client_retry_attempts_total",
            description="Retry attempts initiated by httpclient",
        ),
        breaker_transitions=m.create_counter(
            "http_client_breaker_transitions_total",
            description="Circuit breaker state transitions per upstream host",
        ),
        breaker_open=m.create_up_down_counter(
            "http_client_breaker_open",
            description="1 while the host breaker is open",
        ),
    )


def host_label(request: httpx.Request) -> str:
    """Label `host` метрик и ключ цепи — `host[:port]` запроса."""
    return request.url.netloc.decode()


def redacted_url(url: httpx.URL) -> str:
    """URL без query, фрагмента и userinfo: токен в них не уедет ни в трейс, ни в текст ошибки."""
    return str(url.copy_with(query=None, fragment=None, userinfo=b""))


def span_attributes(request: httpx.Request) -> dict[str, str]:
    """Атрибуты спана вызова; `url.full` без query и userinfo — секрет не уедет в трейс."""
    return {
        "http.request.method": request.method,
        "server.address": request.url.host,
        "url.full": redacted_url(request.url),
    }


def record_response(span: Span, response: httpx.Response) -> None:
    """Ставит на спан код ответа и статус ошибки для 4xx/5xx."""
    span.set_attribute("http.response.status_code", response.status_code)
    if response.status_code >= 400:
        span.set_status(Status(StatusCode.ERROR, f"HTTP {response.status_code}"))
