"""HTTPInstruments — общий слой OTel-инструментов для всех middleware'ей. Берёт
глобальный meter `metrics.get_meter("app")` (telemetry.init выставляет provider;
без telemetry — no-op). Имена метрик: http_requests_total,
http_request_duration_seconds, http_panics_total,
http_max_body_rejections_total, http_idempotency_total.
"""

from __future__ import annotations

from opentelemetry import metrics
from opentelemetry.metrics import Counter, Histogram, Meter

_DURATION_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30]


class HTTPInstruments:
    req_total: Counter
    req_duration: Histogram
    panics_total: Counter
    max_body_rejections: Counter
    idempotency_total: Counter

    def __init__(self, meter: Meter) -> None:
        self.req_total = meter.create_counter(
            "http_requests_total",
            description="Total number of HTTP requests",
        )
        self.req_duration = meter.create_histogram(
            "http_request_duration_seconds",
            description="HTTP request duration in seconds",
            explicit_bucket_boundaries_advisory=_DURATION_BUCKETS,
        )
        self.panics_total = meter.create_counter(
            "http_panics_total",
            description="Exceptions recovered in HTTP handler chain",
        )
        self.max_body_rejections = meter.create_counter(
            "http_max_body_rejections_total",
            description="Requests rejected because body exceeded MaxBytes",
        )
        self.idempotency_total = meter.create_counter(
            "http_idempotency_total",
            description="Idempotency middleware outcomes (stored|replayed)",
        )


def new_instruments() -> HTTPInstruments:
    return HTTPInstruments(metrics.get_meter("app"))
