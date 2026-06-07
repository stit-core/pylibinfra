"""Unit-тесты in-memory audittest.Recorder (без Docker)."""

from __future__ import annotations

from pylibinfra.auditkit import Entry
from pylibinfra.auditkit import Recorder as RecorderPort
from pylibinfra.testkit.audittest import Recorder


async def test_recorder_round_trip() -> None:
    r = Recorder()
    # Удовлетворяет порту auditkit.Recorder.
    _: RecorderPort = r

    await r.record(Entry(action="login", resource="auth", actor_id="u1"))
    await r.record(Entry(action="register", resource="user", actor_id="u1"))
    await r.record(Entry(action="login", resource="auth", actor_id="u2"))

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
