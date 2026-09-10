import pytest
from opentelemetry.metrics import Meter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from pylibinfra.bizmetric import (
    UNIT_EVENT,
    UNIT_ITEM,
    UNIT_SECONDS,
    InvalidNameError,
    counter,
    gauge,
    histogram,
    must_name,
    up_down_counter,
)


@pytest.fixture
def meter_reader() -> tuple[Meter, InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    return provider.get_meter("test"), reader


def _collect(reader: InMemoryMetricReader) -> dict[str, tuple[str, str, list[float]]]:
    # Один сбор на тест: reader отдаёт точки один раз, второй get_metrics_data пуст.
    data = reader.get_metrics_data()
    if data is None:
        return {}
    return {
        metric.name: (
            type(metric.data).__name__,
            metric.unit,
            [point.value for point in metric.data.data_points],
        )
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
    }


def test_must_name() -> None:
    assert must_name("auth", "login_attempts") == "biz_auth_login_attempts"


def test_counter_is_total_with_event_unit(meter_reader: tuple[Meter, InMemoryMetricReader]) -> None:
    meter, reader = meter_reader
    counter(meter, "auth", "login_attempts").add(1, {"result": "ok"})

    assert _collect(reader)["biz_auth_login_attempts_total"] == ("Sum", UNIT_EVENT, [1])


def test_histogram_is_seconds_with_shared_buckets(
    meter_reader: tuple[Meter, InMemoryMetricReader],
) -> None:
    meter, reader = meter_reader
    histogram(meter, "onboarding", "duration").record(1.5)

    data = reader.get_metrics_data()
    assert data is not None
    found = [
        metric
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
        if metric.name == "biz_onboarding_duration_seconds"
    ]
    assert len(found) == 1
    assert found[0].unit == UNIT_SECONDS
    # Общая сетка репозитория: своих бакетов кит не заводит.
    assert list(found[0].data.data_points[0].explicit_bounds)[-1] == 30


def test_gauge_records_current_value(meter_reader: tuple[Meter, InMemoryMetricReader]) -> None:
    meter, reader = meter_reader
    g = gauge(meter, "checkout", "active_carts")
    g.set(42)
    g.set(7)

    assert _collect(reader)["biz_checkout_active_carts"] == ("Gauge", UNIT_ITEM, [7])


def test_up_down_counter_accumulates_deltas(
    meter_reader: tuple[Meter, InMemoryMetricReader],
) -> None:
    meter, reader = meter_reader
    c = up_down_counter(meter, "checkout", "carts_open")
    c.add(3)
    c.add(-1)

    assert _collect(reader)["biz_checkout_carts_open"] == ("Sum", UNIT_ITEM, [2])


def test_unit_and_description_overridable(meter_reader: tuple[Meter, InMemoryMetricReader]) -> None:
    meter, reader = meter_reader
    counter(meter, "billing", "bytes_sent", description="payload", unit="By").add(1)

    assert _collect(reader)["biz_billing_bytes_sent_total"] == ("Sum", "By", [1])


@pytest.mark.parametrize(
    ("domain", "name"),
    [
        ("Auth", "x"),
        ("auth", "Name"),
        ("1auth", "x"),
        ("auth", "with-dash"),
        ("", "x"),
        ("auth", ""),
        ("auth", "with space"),
        ("auth\n", "x"),
        ("auth", "x\n"),
        ("auth", "\nx"),
    ],
)
def test_invalid_shape_rejected(domain: str, name: str) -> None:
    with pytest.raises(InvalidNameError, match=r"\^\[a-z\]\[a-z0-9_\]\*\$"):
        must_name(domain, name)


@pytest.mark.parametrize(("domain", "name"), [("biz_auth", "x"), ("auth", "biz_x")])
def test_biz_prefix_rejected(domain: str, name: str) -> None:
    with pytest.raises(InvalidNameError, match="prefix biz_ is added by the helper"):
        must_name(domain, name)


@pytest.mark.parametrize(
    "name",
    ["logins_total", "wait_seconds", "items_count", "bytes_sum", "latency_bucket", "at_created"],
)
def test_reserved_suffix_rejected(name: str) -> None:
    with pytest.raises(InvalidNameError, match="is reserved"):
        must_name("auth", name)

    with pytest.raises(InvalidNameError, match="is reserved"):
        must_name(name, "x")


def test_invalid_name_error_is_value_error() -> None:
    assert issubclass(InvalidNameError, ValueError)


def test_factories_validate_before_touching_meter(
    meter_reader: tuple[Meter, InMemoryMetricReader],
) -> None:
    meter, reader = meter_reader
    for factory in (counter, histogram, gauge, up_down_counter):
        with pytest.raises(InvalidNameError):
            factory(meter, "auth", "logins_total")

    assert _collect(reader) == {}
