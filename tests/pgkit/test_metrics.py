from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from pylibinfra.pgkit import instrument_pool


class FakePool:
    def get_size(self) -> int:
        return 5

    def get_idle_size(self) -> int:
        return 3

    def get_max_size(self) -> int:
        return 10

    def get_min_size(self) -> int:
        return 2


def test_instrument_pool_emits_pool_gauges() -> None:
    reader = InMemoryMetricReader()
    mp = MeterProvider(metric_readers=[reader])
    meter = mp.get_meter("pgkit-test")

    instrument_pool(FakePool(), meter)  # type: ignore[arg-type]

    data = reader.get_metrics_data()
    values: dict[str, float] = {}
    assert data is not None
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                for point in m.data.data_points:
                    values[m.name] = point.value

    assert values["db_pool_total_conns"] == 5
    assert values["db_pool_idle_conns"] == 3
    assert values["db_pool_acquired_conns"] == 2
    assert values["db_pool_max_conns"] == 10
    assert values["db_pool_min_conns"] == 2
