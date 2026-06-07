from dataclasses import dataclass

from opentelemetry import metrics
from opentelemetry.metrics import Counter, Histogram, UpDownCounter


@dataclass
class Instruments:
    breaker_transitions: Counter
    breaker_open: UpDownCounter
    retry_attempts: Counter
    request_duration: Histogram


def new_instruments() -> Instruments:
    m = metrics.get_meter("app")
    return Instruments(
        breaker_transitions=m.create_counter(
            "http_client_breaker_transitions_total",
            description="Circuit breaker state transitions",
        ),
        breaker_open=m.create_up_down_counter(
            "http_client_breaker_open",
            description="1 if breaker is open, 0 if closed/half-open",
        ),
        retry_attempts=m.create_counter(
            "http_client_retry_attempts_total",
            description="Retry attempts initiated by httpclient",
        ),
        request_duration=m.create_histogram(
            "http_client_request_duration_seconds",
            unit="s",
            description="Outbound HTTP request duration",
        ),
    )
