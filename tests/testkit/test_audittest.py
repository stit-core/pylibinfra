"""Unit-тесты in-memory audittest.Recorder (без Docker)."""

from __future__ import annotations

from datetime import datetime

import pytest

from pylibinfra.auditkit import Entry, InvalidEntryError
from pylibinfra.auditkit import Logger as LoggerPort
from pylibinfra.testkit.audittest import Recorder


async def test_recorder_round_trip() -> None:
    r = Recorder()
    _: LoggerPort = r

    await r.log(Entry(action="login", resource="auth", actor_id="u1"))
    await r.log(Entry(action="register", resource="user", actor_id="u1"))
    await r.log(Entry(action="login", resource="auth", actor_id="u2"))

    assert r.actions() == ["login", "register", "login"]
    assert r.has("login") is True
    assert r.has("missing") is False

    last = r.last("login")
    assert last is not None
    assert last.actor_id == "u2"
    assert r.last("missing") is None

    by_user = r.by_resource("user")
    assert len(by_user) == 1
    assert by_user[0].action == "register"

    assert len(r.entries()) == 3
    r.reset()
    assert r.entries() == []


async def test_recorder_validates_like_pgstore() -> None:
    r = Recorder()
    with pytest.raises(InvalidEntryError):
        await r.log(Entry(action="", resource="user"))
    with pytest.raises(InvalidEntryError):
        await r.log(Entry(action="x", resource=""))
    with pytest.raises(InvalidEntryError, match="at must be timezone-aware"):
        await r.log(Entry(action="x", resource="u", at=datetime(2026, 6, 7, 12, 0)))
    assert r.entries() == []


async def test_recorder_copies_metadata() -> None:
    r = Recorder()
    metadata = {"n": 1}
    await r.log(Entry(action="x", resource="u", metadata=metadata))
    metadata["n"] = 2

    stored = r.entries()[0]
    assert stored.metadata == {"n": 1}

    # Копия отдаётся и наружу: мутация ответа не портит историю.
    stored.metadata["n"] = 3  # type: ignore[index]
    assert r.entries()[0].metadata == {"n": 1}
