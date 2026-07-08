from __future__ import annotations

from typing import Any

import pytest

from pylibinfra.natskit import (
    DisabledStreamBus,
    JetStreamDisabledError,
    StreamMsg,
    publish_stream_json,
)


def test_stream_msg_defaults() -> None:
    m = StreamMsg(subject="a.b", payload=b"{}")
    assert m.msg_id == ""
    m2 = StreamMsg(subject="a.b", payload=b"{}", msg_id="42")
    assert m2.msg_id == "42"


async def test_disabled_stream_bus_raises() -> None:
    bus = DisabledStreamBus()
    with pytest.raises(JetStreamDisabledError):
        await bus.publish_stream("x.y", b"{}")
    with pytest.raises(JetStreamDisabledError):
        await bus.publish_stream_batch([StreamMsg(subject="x.y", payload=b"{}")])

    async def _h(_: bytes) -> None:  # pragma: no cover
        return

    with pytest.raises(JetStreamDisabledError):
        await bus.subscribe_stream("s", "x.y", "dur", _h)
    # drain — no-op на выключенном
    await bus.drain()


async def test_publish_stream_json_passes_msg_id() -> None:
    class FakeBus:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bytes, str | None]] = []

        async def publish_stream(
            self, subject: str, payload: bytes, msg_id: str | None = None
        ) -> None:
            self.calls.append((subject, payload, msg_id))

        async def publish_stream_batch(self, msgs: list[StreamMsg]) -> list[Exception | None]:
            raise NotImplementedError

        async def subscribe_stream(
            self, stream: str, subject: str, durable: str, handler: Any
        ) -> None:
            raise NotImplementedError

        async def drain(self) -> None:
            return

    bus = FakeBus()
    await publish_stream_json(bus, "orders.created", {"id": 1}, "dedup-1")  # type: ignore[arg-type]
    assert bus.calls == [("orders.created", b'{"id":1}', "dedup-1")]
