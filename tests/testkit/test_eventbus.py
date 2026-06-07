"""Unit-тесты in-memory eventbus.Recorder (без Docker)."""

from __future__ import annotations

from pylibinfra.outboxkit import Publisher
from pylibinfra.testkit.eventbus import Recorder


async def test_publish_and_read() -> None:
    r = Recorder()
    # Удовлетворяет порту outboxkit.Publisher.
    _: Publisher = r

    await r.publish("user.created", "u1")
    await r.publish("user.created", "u2")
    await r.publish("auth.login", "u1")

    assert r.count() == 3
    assert r.published("user.created") == ["u1", "u2"]
    assert r.last("auth.login") == "u1"
    assert r.last("missing") is None

    events = r.all()
    assert events[0].subject == "user.created"
    assert events[0].payload == "u1"

    r.reset()
    assert r.count() == 0
    assert r.published("user.created") == []
