import pytest
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader


def _read_total(reader: InMemoryMetricReader, name: str) -> float:
    data = reader.get_metrics_data()
    if data is None:
        return 0.0
    acc = 0.0
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name != name:
                    continue
                for point in metric.data.data_points:
                    acc += getattr(point, "value", 0) or 0
    return acc


class _Metrics:
    def __init__(self, reader: InMemoryMetricReader) -> None:
        self._reader = reader

    def total(self, name: str) -> float:
        return _read_total(self._reader, name)


@pytest.fixture
def metrics_view() -> _Metrics:
    # OTel держит MeterProvider set-once глобально; сбрасываем guard, иначе
    # set_meter_provider молча игнорируется и reader не привязан. Provider — ДО
    # создания Client (инструменты резолвят get_meter("app") лениво в __init__).
    from opentelemetry.metrics import _internal as metrics_internal
    from opentelemetry.util._once import Once

    metrics_internal._METER_PROVIDER_SET_ONCE = Once()
    metrics_internal._METER_PROVIDER = None
    reader = InMemoryMetricReader()
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
    return _Metrics(reader)
