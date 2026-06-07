import pytest
from opentelemetry.metrics import Meter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from pylibinfra.bizmetric import counter, gauge, histogram, must_name


def _meter_with_reader() -> tuple[Meter, InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    return provider.get_meter("test"), reader


def _metric_names(reader: InMemoryMetricReader) -> set[str]:
    data = reader.get_metrics_data()
    if data is None:
        return set()
    return {
        metric.name
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
    }


def test_must_name() -> None:
    assert must_name("auth", "login_attempts") == "biz_auth_login_attempts"


def test_counter_suffix_total() -> None:
    meter, reader = _meter_with_reader()
    c = counter(meter, "auth", "login_attempts")
    c.add(1, {"result": "ok"})
    assert "biz_auth_login_attempts_total" in _metric_names(reader)


def test_histogram_suffix_seconds() -> None:
    meter, reader = _meter_with_reader()
    h = histogram(meter, "http", "request_latency")
    h.record(0.1)
    assert "biz_http_request_latency_seconds" in _metric_names(reader)


def test_gauge_no_suffix() -> None:
    meter, reader = _meter_with_reader()
    g = gauge(meter, "queue", "depth")
    g.add(3)
    assert "biz_queue_depth" in _metric_names(reader)


@pytest.mark.parametrize(
    ("domain", "name"),
    [("Auth", "x"), ("auth", "Name"), ("1auth", "x"), ("auth", "with-dash"), ("", "x")],
)
def test_invalid_names_raise(domain: str, name: str) -> None:
    with pytest.raises(ValueError, match="bizmetric"):
        must_name(domain, name)
