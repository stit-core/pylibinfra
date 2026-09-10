from datetime import UTC, datetime, timedelta, timezone

import pytest

from pylibinfra.auditkit import (
    MAX_ACTION_LEN,
    MAX_ACTOR_ID_LEN,
    MAX_RESOURCE_ID_LEN,
    MAX_RESOURCE_LEN,
    AuditError,
    Entry,
    InvalidEntryError,
    Logger,
)


def test_entry_defaults_are_empty() -> None:
    entry = Entry(action="login", resource="session")
    assert entry.actor_id == ""
    assert entry.resource_id == ""
    assert entry.ip == ""
    assert entry.request_id == ""
    assert entry.metadata == {}
    assert entry.at is None


def test_valid_entry_passes() -> None:
    Entry(
        action="user.create",
        resource="user",
        actor_id="a" * MAX_ACTOR_ID_LEN,
        resource_id="r" * MAX_RESOURCE_ID_LEN,
        metadata={"n": 7},
        at=datetime(2026, 6, 7, 12, 0, tzinfo=UTC),
        ip="203.0.113.7",
        request_id="req-1",
    ).validate()


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (Entry(action="", resource="user"), "action is required"),
        (Entry(action="x", resource=""), "resource is required"),
        (Entry(action="x", resource="u", actor_id="a" * (MAX_ACTOR_ID_LEN + 1)), "actor_id"),
        (Entry(action="a" * (MAX_ACTION_LEN + 1), resource="u"), "action is longer"),
        (Entry(action="x", resource="r" * (MAX_RESOURCE_LEN + 1)), "resource is longer"),
        (
            Entry(action="x", resource="u", resource_id="r" * (MAX_RESOURCE_ID_LEN + 1)),
            "resource_id",
        ),
    ],
)
def test_invalid_entries_are_rejected(entry: Entry, message: str) -> None:
    with pytest.raises(InvalidEntryError, match=message):
        entry.validate()


def test_naive_at_is_rejected() -> None:
    # Naive datetime драйвер записал бы в TIMESTAMPTZ по часовому поясу процесса.
    with pytest.raises(InvalidEntryError, match="at must be timezone-aware"):
        Entry(action="x", resource="u", at=datetime(2026, 6, 7, 12, 0)).validate()


def test_aware_at_in_any_zone_is_accepted() -> None:
    zone = timezone(timedelta(hours=3))
    Entry(action="x", resource="u", at=datetime(2026, 6, 7, 12, 0, tzinfo=zone)).validate()


def test_error_hierarchy() -> None:
    assert issubclass(InvalidEntryError, AuditError)
    assert issubclass(InvalidEntryError, ValueError)


def test_logger_protocol_is_structural() -> None:
    class Impl:
        async def log(self, entry: Entry) -> None:
            return None

    assert isinstance(Impl(), Logger)
    assert not isinstance(object(), Logger)
