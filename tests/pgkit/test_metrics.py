from collections.abc import Callable

from pylibinfra.pgkit import ROLE_PRIMARY, ROLE_REPLICA, instrument_pool


class FakePool:
    def __init__(self, size: int = 5, idle: int = 3, closing: bool = False) -> None:
        self._size = size
        self._idle = idle
        self._closing = closing

    def get_size(self) -> int:
        return self._size

    def get_idle_size(self) -> int:
        return self._idle

    def get_max_size(self) -> int:
        return 10

    def get_min_size(self) -> int:
        return 2

    def is_closing(self) -> bool:
        return self._closing


def test_instrument_pool_emits_pool_gauges(read_metric: Callable[..., float]) -> None:
    registration = instrument_pool(FakePool(), ROLE_PRIMARY)
    try:
        attrs = {"role": "primary"}
        assert read_metric("pg_pool_total_conns", attrs) == 5
        assert read_metric("pg_pool_idle_conns", attrs) == 3
        assert read_metric("pg_pool_acquired_conns", attrs) == 2
        assert read_metric("pg_pool_max_conns", attrs) == 10
        assert read_metric("pg_pool_min_conns", attrs) == 2
    finally:
        registration.unregister()


def test_both_roles_are_observed(read_metric: Callable[..., float]) -> None:
    primary = instrument_pool(FakePool(size=5, idle=1), ROLE_PRIMARY)
    replica = instrument_pool(FakePool(size=9, idle=9), ROLE_REPLICA)
    try:
        assert read_metric("pg_pool_total_conns", {"role": "primary"}) == 5
        assert read_metric("pg_pool_total_conns", {"role": "replica"}) == 9
    finally:
        primary.unregister()
        replica.unregister()


def test_unregister_stops_observation(read_metric: Callable[..., float]) -> None:
    registration = instrument_pool(FakePool(), ROLE_PRIMARY)
    registration.unregister()
    assert read_metric("pg_pool_total_conns", {"role": "primary"}) == 0


def test_unregister_keeps_pool_that_took_over_the_role(read_metric: Callable[..., float]) -> None:
    displaced = instrument_pool(FakePool(size=5, idle=1), ROLE_PRIMARY)
    current = instrument_pool(FakePool(size=9, idle=2), ROLE_PRIMARY)
    try:
        displaced.unregister()
        assert read_metric("pg_pool_total_conns", {"role": "primary"}) == 9
    finally:
        current.unregister()


def test_closing_pool_is_skipped(read_metric: Callable[..., float]) -> None:
    registration = instrument_pool(FakePool(closing=True), ROLE_PRIMARY)
    try:
        assert read_metric("pg_pool_total_conns", {"role": "primary"}) == 0
    finally:
        registration.unregister()
