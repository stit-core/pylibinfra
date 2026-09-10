"""Unit-тесты фейков bruteforcetest (без Docker)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pylibinfra.bruteforcekit import Cleaner, Config, ConfigError, Guard, InvalidKeyError
from pylibinfra.clockkit import Tickable
from pylibinfra.testkit.bruteforcetest import Attempt, Counting, Locked, Permissive, Recorder

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_CFG = Config.model_validate(
    {"max_failures": 3, "lock_duration": 900.0, "cleanup_interval": 60.0, "ipv6_prefix": 64}
)


def _fakes() -> list[Guard]:
    return [Permissive(), Locked(_NOW + timedelta(minutes=5)), Recorder(), Counting(_CFG)]


def test_all_fakes_satisfy_guard_protocol() -> None:
    for fake in _fakes():
        assert isinstance(fake, Guard)


def test_only_counting_is_a_cleaner() -> None:
    assert isinstance(Counting(_CFG), Cleaner)
    for fake in (Permissive(), Locked(_NOW), Recorder()):
        assert not isinstance(fake, Cleaner)


async def test_permissive_never_locks() -> None:
    guard = Permissive()
    assert await guard.check("alice", "1.2.3.4") is None
    assert await guard.record_failure("alice", "1.2.3.4") is None
    await guard.record_success("alice", "1.2.3.4")


async def test_locked_returns_lock_state() -> None:
    until = _NOW + timedelta(minutes=5)
    guard = Locked(until)
    state = await guard.check("alice", "1.2.3.4")
    assert state is not None
    assert state.until == until
    failure = await guard.record_failure("alice", "1.2.3.4")
    assert failure is not None
    assert failure.until == until


async def test_recorder_tracks_failure_and_success() -> None:
    rec = Recorder()
    await rec.record_failure("alice", "1.2.3.4")
    await rec.record_failure("bob", "5.6.7.8")
    await rec.record_success("alice", "1.2.3.4")

    assert rec.failures() == ["alice", "bob"]
    assert rec.successes() == ["alice"]
    assert rec.attempts() == [Attempt("alice", "1.2.3.4"), Attempt("bob", "5.6.7.8")]
    assert rec.success_attempts() == [Attempt("alice", "1.2.3.4")]
    assert await rec.check("alice", "1.2.3.4") is None


@pytest.mark.parametrize(("subject", "ip"), [("", "1.2.3.4"), ("u", ""), ("u", "nope")])
async def test_fakes_reject_invalid_key(subject: str, ip: str) -> None:
    for fake in _fakes():
        with pytest.raises(InvalidKeyError):
            await fake.check(subject, ip)
        with pytest.raises(InvalidKeyError):
            await fake.record_failure(subject, ip)
        with pytest.raises(InvalidKeyError):
            await fake.record_success(subject, ip)


async def test_invalid_key_never_reaches_recorder_history() -> None:
    rec = Recorder()
    with pytest.raises(InvalidKeyError):
        await rec.record_failure("alice", "")
    assert rec.attempts() == []


async def test_counting_locks_after_max_failures() -> None:
    clock = Tickable(_NOW)
    guard = Counting(_CFG, clock)

    for _ in range(_CFG.max_failures - 1):
        assert await guard.record_failure("alice", "1.2.3.4") is None
    assert await guard.check("alice", "1.2.3.4") is None

    state = await guard.record_failure("alice", "1.2.3.4")
    assert state is not None
    assert state.failed_count == _CFG.max_failures
    assert state.until == _NOW + timedelta(seconds=_CFG.lock_duration)


async def test_counting_lock_expires_and_starts_new_window() -> None:
    clock = Tickable(_NOW)
    guard = Counting(_CFG, clock)
    for _ in range(_CFG.max_failures):
        await guard.record_failure("alice", "1.2.3.4")

    clock.advance(timedelta(seconds=_CFG.lock_duration + 1))
    assert await guard.check("alice", "1.2.3.4") is None

    state = await guard.record_failure("alice", "1.2.3.4")
    assert state is None


async def test_counting_success_clears_history() -> None:
    guard = Counting(_CFG, Tickable(_NOW))
    for _ in range(_CFG.max_failures - 1):
        await guard.record_failure("alice", "1.2.3.4")
    await guard.record_success("alice", "1.2.3.4")

    assert await guard.record_failure("alice", "1.2.3.4") is None
    assert await guard.check("alice", "1.2.3.4") is None


async def test_counting_buckets_are_independent() -> None:
    guard = Counting(_CFG, Tickable(_NOW))
    for _ in range(_CFG.max_failures):
        await guard.record_failure("alice", "1.2.3.4")

    assert await guard.check("alice", "1.2.3.4") is not None
    assert await guard.check("alice", "5.6.7.8") is None
    assert await guard.check("bob", "1.2.3.4") is None


async def test_counting_aggregates_ipv6_by_prefix() -> None:
    guard = Counting(_CFG, Tickable(_NOW))
    for _ in range(_CFG.max_failures):
        await guard.record_failure("alice", "2001:db8:0:1::5")

    # Другой адрес той же /64 попадает в тот же бакет.
    assert await guard.check("alice", "2001:db8:0:1::99") is not None
    assert await guard.check("alice", "2001:db8:0:2::5") is None


async def test_counting_locks_from_first_failure_at_threshold_one() -> None:
    cfg = Config.model_validate({"max_failures": 1})
    guard = Counting(cfg, Tickable(_NOW))
    assert await guard.record_failure("alice", "1.2.3.4") is not None


async def test_counting_cleanup_keeps_locked_rows() -> None:
    clock = Tickable(_NOW)
    guard = Counting(_CFG, clock)
    await guard.record_failure("stale", "1.1.1.1")
    for _ in range(_CFG.max_failures):
        await guard.record_failure("locked", "2.2.2.2")

    await guard.cleanup()

    assert await guard.check("locked", "2.2.2.2") is not None
    # Разблокированная строка удалена: следующая неудача начинает счёт с единицы.
    state = await guard.record_failure("stale", "1.1.1.1")
    assert state is None


def test_counting_rejects_invalid_config() -> None:
    # Тот же тип ошибки, что у pgstore.Store: фейк держит контракт конструктора.
    with pytest.raises(ConfigError, match="BRUTEFORCE_MAX_FAILURES"):
        Counting(Config.model_construct(max_failures=0))
