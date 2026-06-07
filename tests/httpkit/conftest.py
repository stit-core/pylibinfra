from collections.abc import Callable

import pytest
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader


def metric_value(reader: InMemoryMetricReader, name: str) -> float:
    data = reader.get_metrics_data()
    total = 0.0
    if data is None:
        return total
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name != name:
                    continue
                for point in m.data.data_points:
                    total += getattr(point, "value", 0) or getattr(point, "count", 0)
    return total


@pytest.fixture
def metric_reader() -> InMemoryMetricReader:
    # OTel держит MeterProvider set-once глобально; в тестах сбрасываем guard,
    # иначе set_meter_provider молча игнорируется и reader не привязан. Provider
    # выставляем ДО создания app/instruments — get_meter("app") резолвится лениво.
    from opentelemetry.metrics import _internal as metrics_internal
    from opentelemetry.util._once import Once

    metrics_internal._METER_PROVIDER_SET_ONCE = Once()
    metrics_internal._METER_PROVIDER = None
    reader = InMemoryMetricReader()
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
    return reader


@pytest.fixture
def read_metric(
    metric_reader: InMemoryMetricReader,
) -> Callable[[str], float]:
    return lambda name: metric_value(metric_reader, name)
