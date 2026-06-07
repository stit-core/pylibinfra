from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

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
    from opentelemetry.metrics import _internal as metrics_internal
    from opentelemetry.util._once import Once

    metrics_internal._METER_PROVIDER_SET_ONCE = Once()
    metrics_internal._METER_PROVIDER = None
    reader = InMemoryMetricReader()
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
    return reader


@pytest.fixture
def read_metric(metric_reader: InMemoryMetricReader) -> Callable[[str], float]:
    return lambda name: metric_value(metric_reader, name)


class _FakeScript:
    """Стаб AsyncScript: моделирует throttle-Lua (INCR + EXPIRE на первом инкременте)
    как одну атомарную операцию над in-memory store."""

    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store

    async def __call__(
        self, keys: Sequence[str] | None = None, args: Sequence[Any] | None = None
    ) -> int:
        key = (keys or [""])[0]
        self._store[key] = int(self._store.get(key, 0)) + 1
        return self._store[key]


class FakeRedis:
    """Минимальный async-redis для unit-lane (без Docker): хватает на SET NX EX,
    INCR, EXPIRE и register_script (throttle-Lua), которые использует tgbotkit
    (dedup/throttle/guard)."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    async def set(self, key: str, value: Any, nx: bool = False, ex: Any = None) -> bool | None:
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True

    async def incr(self, key: str) -> int:
        self._store[key] = int(self._store.get(key, 0)) + 1
        return self._store[key]

    async def expire(self, key: str, ttl: int) -> bool:
        return True

    async def delete(self, key: str) -> int:
        return int(self._store.pop(key, None) is not None)

    def register_script(self, script: str) -> _FakeScript:
        return _FakeScript(self._store)


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()
