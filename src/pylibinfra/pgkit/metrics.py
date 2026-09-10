"""instrument_pool — метрики `pg_pool_*{role}` поверх asyncpg-пула.

Пулы держатся в реестре по `role`, а инструменты создаются один раз на Meter:
OTel-python молча игнорирует повторное создание инструмента с тем же именем, и
второй пул (реплика) иначе остался бы без наблюдения.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from opentelemetry.metrics import CallbackOptions, Meter, Observation

from pylibinfra._internal import otelkit

from .types import Pool

ROLE_PRIMARY = "primary"
ROLE_REPLICA = "replica"

# role → пул под наблюдением; несколько пулов с одним role в одном процессе
# сливаются в одну серию — давай им разные role.
_pools: dict[str, Pool] = {}
_meter: Meter | None = None


class PoolInstrumentation:
    """Регистрация пула в метриках `pg_pool_*`; `unregister` снимает его с наблюдения."""

    def __init__(self, role: str, pool: Pool) -> None:
        self.role = role
        self._pool = pool

    def unregister(self) -> None:
        """Убирает пул из реестра — после закрытия пула его статистику читать нельзя.

        Снимается только свой пул: если тот же `role` успел занять другой пул,
        его регистрация переживает `unregister` вытесненного.
        """
        if _pools.get(self.role) is self._pool:
            del _pools[self.role]


def instrument_pool(pool: Pool, role: str = ROLE_PRIMARY) -> PoolInstrumentation:
    """Ставит пул под метрики `pg_pool_*` с label `role`; снятие — `unregister()`."""
    global _meter
    m = otelkit.meter("pgkit")
    if _meter is not m:
        _create_gauges(m)
        _meter = m
    _pools[role] = pool
    return PoolInstrumentation(role, pool)


def _observe(stat: str) -> Iterable[Observation]:
    for role, pool in list(_pools.items()):
        if pool.is_closing():
            continue
        size, idle = pool.get_size(), pool.get_idle_size()
        value = {
            "acquired": size - idle,
            "idle": idle,
            "total": size,
            "max": pool.get_max_size(),
            "min": pool.get_min_size(),
        }[stat]
        yield Observation(value, {"role": role})


def _create_gauges(m: Meter) -> None:
    gauges = (
        ("pg_pool_acquired_conns", "acquired", "Connections currently acquired from pool"),
        ("pg_pool_idle_conns", "idle", "Idle connections in pool"),
        ("pg_pool_total_conns", "total", "Total open connections in pool"),
        ("pg_pool_max_conns", "max", "Configured pool maximum"),
        ("pg_pool_min_conns", "min", "Configured pool minimum"),
    )
    for name, stat, description in gauges:
        m.create_observable_gauge(
            name,
            callbacks=[_callback(stat)],
            description=description,
        )


def _callback(stat: str) -> Callable[[CallbackOptions], Iterable[Observation]]:
    def observe(_: CallbackOptions) -> Iterable[Observation]:
        return _observe(stat)

    return observe
