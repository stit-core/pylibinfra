"""instrument_pool — ObservableGauge'и db_pool_* поверх asyncpg-пула.

Эмитируемые метрики:

    db_pool_acquired_conns  — занятые соединения (size − idle)
    db_pool_idle_conns      — простаивающие
    db_pool_total_conns     — всего открытых
    db_pool_max_conns       — лимит
    db_pool_min_conns       — нижняя граница пула

asyncpg не отдаёт cumulative acquire-counters (в отличие от pgx), поэтому
counter-метрики из golibinfra здесь не воспроизводимы — только pool-state
gauges. Все метрики attribute-less per-pool.
"""

from collections.abc import Iterable

from opentelemetry.metrics import CallbackOptions, Meter, Observation

from .types import Pool


def instrument_pool(pool: Pool, meter: Meter) -> None:
    def _acquired(_: CallbackOptions) -> Iterable[Observation]:
        yield Observation(pool.get_size() - pool.get_idle_size())

    def _idle(_: CallbackOptions) -> Iterable[Observation]:
        yield Observation(pool.get_idle_size())

    def _total(_: CallbackOptions) -> Iterable[Observation]:
        yield Observation(pool.get_size())

    def _max(_: CallbackOptions) -> Iterable[Observation]:
        yield Observation(pool.get_max_size())

    def _min(_: CallbackOptions) -> Iterable[Observation]:
        yield Observation(pool.get_min_size())

    meter.create_observable_gauge(
        "db_pool_acquired_conns",
        callbacks=[_acquired],
        description="Connections currently acquired from pool",
    )
    meter.create_observable_gauge(
        "db_pool_idle_conns",
        callbacks=[_idle],
        description="Idle connections in pool",
    )
    meter.create_observable_gauge(
        "db_pool_total_conns",
        callbacks=[_total],
        description="Total open connections in pool",
    )
    meter.create_observable_gauge(
        "db_pool_max_conns",
        callbacks=[_max],
        description="Configured pool maximum",
    )
    meter.create_observable_gauge(
        "db_pool_min_conns",
        callbacks=[_min],
        description="Configured pool minimum",
    )
