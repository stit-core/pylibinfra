from datetime import UTC, datetime, timedelta

from pylibinfra.clockkit import Clock, Fixed, Real, Tickable


def test_real_returns_aware_now() -> None:
    clock: Clock = Real()
    now = clock.now()
    assert now.tzinfo is not None


def test_fixed_is_constant() -> None:
    t = datetime(2020, 1, 1, tzinfo=UTC)
    clock = Fixed(t)
    assert clock.now() == t
    assert clock.now() == t


def test_tickable_advances() -> None:
    t = datetime(2020, 1, 1, tzinfo=UTC)
    clock = Tickable(t)
    assert clock.now() == t
    clock.advance(timedelta(seconds=5))
    assert clock.now() == t + timedelta(seconds=5)
