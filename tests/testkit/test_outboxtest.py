"""outboxtest.Recorder: фейк outboxkit.Publisher для тестов домена."""

from __future__ import annotations

import pytest

from pylibinfra.outboxkit import OutboxError, Publisher
from pylibinfra.testkit import outboxtest


def _publisher(recorder: outboxtest.Recorder) -> Publisher:
    return recorder


async def test_publish_and_read() -> None:
    recorder = outboxtest.new()

    await _publisher(recorder).publish("user.created", {"id": "u1"})
    await recorder.publish("user.created", {"id": "u2"})
    await recorder.publish("auth.login", {"id": "u1"})

    assert recorder.count() == 3
    assert recorder.published("user.created") == [{"id": "u1"}, {"id": "u2"}]
    assert recorder.last("auth.login") == {"id": "u1"}
    assert recorder.last("missing") is None
    assert recorder.published("missing") == []


async def test_all_returns_copy_in_order() -> None:
    recorder = outboxtest.Recorder()
    await recorder.publish("a", 1)
    await recorder.publish("b", 2)

    events = recorder.all()
    assert [e.subject for e in events] == ["a", "b"]
    events.clear()
    assert len(recorder.all()) == 2


async def test_reset_clears_events() -> None:
    recorder = outboxtest.new()
    await recorder.publish("a", 1)

    recorder.reset()

    assert recorder.count() == 0


async def test_rejects_unserializable_payload_like_the_real_bus() -> None:
    recorder = outboxtest.new()

    with pytest.raises(OutboxError, match="marshal payload"):
        await recorder.publish("a", object())

    assert recorder.count() == 0
