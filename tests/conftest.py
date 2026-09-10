"""Общие фикстуры: перехват метрик и спанов китов через in-memory OTel SDK."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import Any

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.util._once import Once


def _reset_meter_provider() -> None:
    # OTel держит провайдеры set-once глобально; в тестах сбрасываем guard,
    # иначе set_meter_provider молча игнорируется и reader не привязан.
    from opentelemetry.metrics import _internal as metrics_internal

    metrics_internal._METER_PROVIDER_SET_ONCE = Once()
    metrics_internal._METER_PROVIDER = None


def _reset_tracer_provider() -> None:
    trace._TRACER_PROVIDER_SET_ONCE = Once()
    trace._TRACER_PROVIDER = None


def metric_value(
    reader: InMemoryMetricReader, name: str, attrs: Mapping[str, Any] | None = None
) -> float:
    """Сумма точек метрики `name` (counter — value, histogram — count) с подмножеством attrs.

    Сбор идёт на вызывающем loop'е: observable-callback, ждущий этот же loop, повис бы.
    """
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
                    if attrs and any(point.attributes.get(k) != v for k, v in attrs.items()):
                        continue
                    total += getattr(point, "value", 0) or getattr(point, "count", 0)
    return total


@pytest.fixture
def metric_reader() -> Iterator[InMemoryMetricReader]:
    """MeterProvider с in-memory reader'ом; ставится ДО создания объектов кита."""
    _reset_meter_provider()
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)
    yield reader
    provider.shutdown()


@pytest.fixture
def read_metric(
    metric_reader: InMemoryMetricReader,
) -> Callable[..., float]:
    """`read_metric(name, attrs=None)` — значение метрики из `metric_reader`."""
    return lambda name, attrs=None: metric_value(metric_reader, name, attrs)


@pytest.fixture
def span_exporter() -> Iterator[InMemorySpanExporter]:
    """TracerProvider с in-memory экспортёром спанов; ставится ДО создания объектов кита."""
    _reset_tracer_provider()
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield exporter
    provider.shutdown()
