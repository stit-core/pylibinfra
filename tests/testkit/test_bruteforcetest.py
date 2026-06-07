"""Unit-тесты in-memory bruteforcetest.Store через bruteforcekit.Guard (без Docker)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pylibinfra.bruteforcekit import Guard, Store
from pylibinfra.clockkit import Fixed
from pylibinfra.testkit.bruteforcetest import Locked, Permissive, Recorder


def _now() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


async def test_permissive_never_locks() -> None:
    store: Store = Permissive()
    guard = Guard(store, Fixed(_now()))
    assert await guard.check("alice", "1.2.3.4") is None


async def test_locked_returns_lock_state() -> None:
    until = _now() + timedelta(minutes=5)
    guard = Guard(Locked(until), Fixed(_now()))
    state = await guard.check("alice", "1.2.3.4")
    assert state is not None
    assert state.until == until


async def test_locked_expired_is_unlocked() -> None:
    past = _now() - timedelta(minutes=5)
    guard = Guard(Locked(past), Fixed(_now()))
    assert await guard.check("alice", "1.2.3.4") is None


async def test_recorder_tracks_failure_and_success() -> None:
    rec = Recorder()
    guard = Guard(rec, Fixed(_now()))

    await guard.record_failure("alice", "1.2.3.4")
    await guard.record_failure("bob", "5.6.7.8")
    await guard.record_success("alice", "1.2.3.4")

    assert rec.failures() == ["alice", "bob"]
    assert rec.resets() == ["alice"]
    assert await guard.check("alice", "1.2.3.4") is None
