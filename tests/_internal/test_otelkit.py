from importlib.metadata import PackageNotFoundError

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from pylibinfra._internal import otelkit


def test_scope_and_version() -> None:
    assert otelkit.scope("pgkit") == "pylibinfra.pgkit"
    assert otelkit.version()  # версия дистрибутива или "dev"


def test_version_falls_back_without_package_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_: str) -> str:
        raise PackageNotFoundError(otelkit.DISTRIBUTION)

    monkeypatch.setattr(otelkit, "_distribution_version", missing)
    otelkit.version.cache_clear()
    try:
        assert otelkit.version() == otelkit.DEV_VERSION
    finally:
        otelkit.version.cache_clear()


def test_tracer_carries_the_kit_scope(span_exporter: InMemorySpanExporter) -> None:
    with otelkit.tracer("natskit").start_as_current_span("op"):
        pass

    scope = span_exporter.get_finished_spans()[0].instrumentation_scope
    assert scope.name == "pylibinfra.natskit"
    assert scope.version == otelkit.version()


def test_duration_histogram_uses_shared_buckets(metric_reader: InMemoryMetricReader) -> None:
    h = otelkit.duration_histogram(otelkit.meter("x"), "x_op_duration_seconds", "d")
    h.record(0.2)
    data = metric_reader.get_metrics_data()
    assert data is not None
    scopes = [sm for rm in data.resource_metrics for sm in rm.scope_metrics]
    assert scopes[0].scope.name == "pylibinfra.x"
    assert scopes[0].scope.version == otelkit.version()
    point = scopes[0].metrics[0].data.data_points[0]
    assert list(point.explicit_bounds) == list(otelkit.DURATION_BUCKETS)
    assert scopes[0].metrics[0].unit == "s"


def test_long_buckets_extend_short() -> None:
    assert (
        otelkit.LONG_DURATION_BUCKETS[: len(otelkit.DURATION_BUCKETS)] == otelkit.DURATION_BUCKETS
    )
    assert otelkit.LONG_DURATION_BUCKETS[-1] == 600
