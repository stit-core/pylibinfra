from __future__ import annotations

from typing import Any

from pylibinfra.natskit import StreamMsg
from pylibinfra.outboxkit import OutMsg, new_stream_transport, new_stream_transport_for


class CapturingBus:
    def __init__(self) -> None:
        self.batch: list[StreamMsg] = []
        self.single: list[tuple[str, bytes, str | None]] = []

    async def publish_stream(self, subject: str, payload: bytes, msg_id: str | None = None) -> None:
        self.single.append((subject, payload, msg_id))

    async def publish_stream_batch(self, msgs: list[StreamMsg]) -> list[Exception | None]:
        self.batch.extend(msgs)
        return [None] * len(msgs)

    async def subscribe_stream(self, stream: str, subject: str, durable: str, handler: Any) -> None:
        raise NotImplementedError

    async def drain(self) -> None:
        return


async def test_msg_id_bare_by_default() -> None:
    bus = CapturingBus()
    tr = new_stream_transport(bus)  # type: ignore[arg-type]
    await tr.publish_batch([OutMsg(id=42, subject="amocrm.x", payload=b"{}")])
    assert bus.batch[0].msg_id == "42"


async def test_msg_id_namespaced() -> None:
    bus = CapturingBus()
    tr = new_stream_transport_for(bus, "account")  # type: ignore[arg-type]
    await tr.publish_batch([OutMsg(id=42, subject="amocrm.x", payload=b"{}")])
    assert bus.batch[0].msg_id == "account:42"


async def test_no_cross_table_collision() -> None:
    bus = CapturingBus()
    co = new_stream_transport_for(bus, "companies")  # type: ignore[arg-type]
    le = new_stream_transport_for(bus, "leads")  # type: ignore[arg-type]
    await co.publish_batch([OutMsg(id=1, subject="x", payload=b"{}")])
    await le.publish_batch([OutMsg(id=1, subject="x", payload=b"{}")])
    # один id в двух namespace'ах обязан давать разные Msg-Id
    assert bus.batch[0].msg_id != bus.batch[1].msg_id


async def test_single_publish_passes_msg_id() -> None:
    bus = CapturingBus()
    tr = new_stream_transport(bus)  # type: ignore[arg-type]
    await tr.publish(OutMsg(id=7, subject="evt", payload=b"{}"))
    assert bus.single == [("evt", b"{}", "7")]
