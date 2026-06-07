"""Unit-тесты Guard на in-memory фейке Store + детерминированных часах
(Fixed/Tickable) — без Docker. Lockout-истечение проверяется продвижением
Tickable, что и есть смысл Clock-инъекции.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from pylibinfra.bruteforcekit import Config, Guard, LockState
from pylibinfra.clockkit import Clock, Fixed, Tickable

_MAX = 3
_LOCK = timedelta(minutes=15)


@dataclass
class _Entry:
    failed_count: int = 0
    locked_until: datetime | None = None


@dataclass
class _FakeStore:
    """Зеркалит SQL pgstore: ключ (subject, ip), инкремент счётчика, выставление
    locked_until при достижении порога. now берётся из того же Clock, что и Guard,
    чтобы окно блокировки было детерминированным."""

    clock: Clock
    rows: dict[tuple[str, str], _Entry] = field(default_factory=dict)

    async def read(self, subject: str, ip: str) -> datetime | None:
        entry = self.rows.get((subject, ip))
        return entry.locked_until if entry else None

    async def record_failure(self, subject: str, ip: str) -> None:
        entry = self.rows.setdefault((subject, ip), _Entry())
        entry.failed_count += 1
        if entry.failed_count >= _MAX:
            entry.locked_until = self.clock.now() + _LOCK
        else:
            entry.locked_until = None

    async def reset(self, subject: str, ip: str) -> None:
        self.rows.pop((subject, ip), None)

    async def cleanup(self) -> None:
        now = self.clock.now()
        for key in list(self.rows):
            entry = self.rows[key]
            if entry.locked_until is None or entry.locked_until < now:
                del self.rows[key]


def _guard(clock: Clock) -> tuple[Guard, _FakeStore]:
    store = _FakeStore(clock=clock)
    return Guard(store, clock), store


async def test_no_lock_below_threshold() -> None:
    clock = Fixed(datetime(2026, 1, 1, tzinfo=UTC))
    guard, _ = _guard(clock)
    for _ in range(_MAX - 1):
        await guard.record_failure("user@example.com", "10.0.0.1")
    assert await guard.check("user@example.com", "10.0.0.1") is None


async def test_locked_after_max_failures() -> None:
    clock = Fixed(datetime(2026, 1, 1, tzinfo=UTC))
    guard, _ = _guard(clock)
    for _ in range(_MAX):
        await guard.record_failure("user@example.com", "10.0.0.1")
    state = await guard.check("user@example.com", "10.0.0.1")
    assert isinstance(state, LockState)
    assert state.until == clock.now() + _LOCK


async def test_lockout_expires_with_time() -> None:
    clock = Tickable(datetime(2026, 1, 1, tzinfo=UTC))
    guard, _ = _guard(clock)
    for _ in range(_MAX):
        await guard.record_failure("user@example.com", "10.0.0.1")
    assert await guard.check("user@example.com", "10.0.0.1") is not None

    clock.advance(_LOCK - timedelta(seconds=1))
    assert await guard.check("user@example.com", "10.0.0.1") is not None

    clock.advance(timedelta(seconds=2))
    assert await guard.check("user@example.com", "10.0.0.1") is None


async def test_record_success_resets() -> None:
    clock = Fixed(datetime(2026, 1, 1, tzinfo=UTC))
    guard, _store = _guard(clock)
    for _ in range(_MAX):
        await guard.record_failure("user@example.com", "10.0.0.1")
    assert await guard.check("user@example.com", "10.0.0.1") is not None

    await guard.record_success("user@example.com", "10.0.0.1")
    assert await guard.check("user@example.com", "10.0.0.1") is None

    # После сброса счётчик с нуля: одна неудача не блокирует.
    await guard.record_failure("user@example.com", "10.0.0.1")
    assert await guard.check("user@example.com", "10.0.0.1") is None


async def test_subject_and_ip_independent() -> None:
    clock = Fixed(datetime(2026, 1, 1, tzinfo=UTC))
    guard, _ = _guard(clock)
    for _ in range(_MAX):
        await guard.record_failure("alice@example.com", "10.0.0.1")

    assert await guard.check("alice@example.com", "10.0.0.1") is not None
    # Тот же subject с другого IP не заблокирован (ключ — пара subject+ip).
    assert await guard.check("alice@example.com", "10.0.0.2") is None
    # Тот же IP с другим subject не заблокирован.
    assert await guard.check("bob@example.com", "10.0.0.1") is None


async def test_cleanup_removes_unlocked_keeps_locked() -> None:
    clock = Tickable(datetime(2026, 1, 1, tzinfo=UTC))
    guard, store = _guard(clock)

    await guard.record_failure("stale@example.com", "1.1.1.1")  # unlocked
    for _ in range(_MAX):
        await guard.record_failure("locked@example.com", "2.2.2.2")  # locked

    await guard.cleanup()

    assert ("stale@example.com", "1.1.1.1") not in store.rows
    assert await guard.check("locked@example.com", "2.2.2.2") is not None


def test_config_defaults(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    for var in (
        "BRUTEFORCE_MAX_FAILURES",
        "BRUTEFORCE_LOCK_DURATION",
        "BRUTEFORCE_WINDOW",
        "BRUTEFORCE_CLEANUP_RETENTION",
    ):
        monkeypatch.delenv(var, raising=False)
    cfg = Config()
    assert cfg.max_failures == 5
    assert cfg.lock_duration == 900.0
    assert cfg.window == 3600.0
    assert cfg.cleanup_retention == 86400.0


def test_config_reads_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("BRUTEFORCE_MAX_FAILURES", "10")
    monkeypatch.setenv("BRUTEFORCE_LOCK_DURATION", "1800")
    monkeypatch.setenv("BRUTEFORCE_WINDOW", "7200")
    monkeypatch.setenv("BRUTEFORCE_CLEANUP_RETENTION", "172800")
    cfg = Config()
    assert cfg.max_failures == 10
    assert cfg.lock_duration == 1800.0
    assert cfg.window == 7200.0
    assert cfg.cleanup_retention == 172800.0
