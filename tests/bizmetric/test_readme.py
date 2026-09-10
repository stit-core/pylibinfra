"""Быстрый старт README bizmetric — исполняемый: имена и типы инструментов из таблицы."""

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from pylibinfra.bizmetric import counter, gauge, histogram, up_down_counter


def test_readme_quickstart() -> None:
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("shop")

    logins = counter(meter, "auth", "login_attempts")
    logins.add(1, {"result": "ok"})

    onboarding = histogram(meter, "onboarding", "duration")
    onboarding.record(1.5)

    carts = gauge(meter, "checkout", "active_carts")
    carts.set(42)

    open_carts = up_down_counter(meter, "checkout", "carts_open")
    open_carts.add(1)

    data = reader.get_metrics_data()
    assert data is not None
    names = {
        metric.name
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
    }
    assert names == {
        "biz_auth_login_attempts_total",
        "biz_onboarding_duration_seconds",
        "biz_checkout_active_carts",
        "biz_checkout_carts_open",
    }
