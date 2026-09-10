"""Общий шов китов к глобальным OTel-провайдерам.

Одно имя instrumentation-scope (`pylibinfra.<kit>`) с версией библиотеки, общие
границы гистограмм длительности и фабрики Meter/Tracer. Без сконфигурированного
провайдера у потребителя инструменты — no-op, кит не падает.
"""

from __future__ import annotations

from functools import cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

from opentelemetry import metrics, trace
from opentelemetry.metrics import Histogram, Meter
from opentelemetry.trace import Tracer

DISTRIBUTION = "pylibinfra"
DEV_VERSION = "dev"

DURATION_BUCKETS: tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)
LONG_DURATION_BUCKETS: tuple[float, ...] = (*DURATION_BUCKETS, 60, 120, 300, 600)


def scope(kit: str) -> str:
    """Имя instrumentation-scope кита — `pylibinfra.<kit>` (otel_scope_name метрик и спанов)."""
    return f"{DISTRIBUTION}.{kit}"


@cache
def version() -> str:
    """Версия pylibinfra в окружении (otel_scope_version); DEV_VERSION без метаданных пакета."""
    try:
        return _distribution_version(DISTRIBUTION)
    except PackageNotFoundError:
        return DEV_VERSION


def meter(kit: str) -> Meter:
    """Meter глобального провайдера со scope и версией кита."""
    return metrics.get_meter(scope(kit), version())


def tracer(kit: str) -> Tracer:
    """Tracer глобального провайдера со scope и версией кита."""
    return trace.get_tracer(scope(kit), version())


def duration_histogram(m: Meter, name: str, description: str, *, long: bool = False) -> Histogram:
    """Гистограмма длительности в секундах с общими границами; `long` — шкала до 600 s."""
    bounds = LONG_DURATION_BUCKETS if long else DURATION_BUCKETS
    return m.create_histogram(
        name,
        unit="s",
        description=description,
        explicit_bucket_boundaries_advisory=list(bounds),
    )
