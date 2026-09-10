"""natstest.Recorder: синхронная доставка, запись публикаций и инжекторы для тестов домена."""

from __future__ import annotations

import pytest

from pylibinfra.natskit import (
    Bus,
    StreamBus,
    StreamMsg,
    headers_from_context,
    publish_json,
    subject_from_context,
    subscribe_json,
    subscribe_stream_json,
)
from pylibinfra.testkit import natstest


def _bus(recorder: natstest.Recorder) -> Bus:
    return recorder


def _stream(recorder: natstest.Recorder) -> StreamBus:
    return recorder


async def test_publish_records_message_and_delivers_synchronously() -> None:
    recorder = natstest.new()
    seen: list[tuple[str, bytes, dict[str, str]]] = []

    async def handler(payload: bytes) -> None:
        seen.append((subject_from_context(), payload, headers_from_context()))

    await recorder.subscribe("orders.*", handler)
    await recorder.publish(
        "orders.created",
        b"payload",
        msg_id="7",
        headers={"Event-Type": "order.created"},
        metric_subject="orders.*",
    )

    # Доставка синхронна: обработчик уже отработал, когда publish вернулся.
    assert seen[0][0] == "orders.created"
    assert seen[0][1] == b"payload"
    assert seen[0][2]["Event-Type"] == "order.created"

    published = recorder.published()
    assert len(published) == 1
    assert published[0].msg_id == "7"
    assert published[0].metric_subject == "orders.*"
    assert published[0].jetstream is False
    assert recorder.published_on("orders.>")[0].subject == "orders.created"
    assert recorder.published_on("leads.>") == []


async def test_queue_group_gets_one_subscriber() -> None:
    recorder = natstest.new()
    hits: list[str] = []

    async def first(_: bytes) -> None:
        hits.append("first")

    async def second(_: bytes) -> None:
        hits.append("second")

    async def fanout(_: bytes) -> None:
        hits.append("fanout")

    await recorder.subscribe_queue("orders.created", "workers", first)
    await recorder.subscribe_queue("orders.created", "workers", second)
    await recorder.subscribe("orders.created", fanout)
    await recorder.publish("orders.created", b"x")

    assert hits == ["first", "fanout"]


async def test_stream_publish_reaches_only_stream_subscribers() -> None:
    recorder = natstest.new()
    core: list[bytes] = []
    stream: list[bytes] = []

    await recorder.subscribe("ORDERS.>", lambda p: _append(core, p))
    await recorder.subscribe_stream("ORDERS", "ORDERS.>", "worker", lambda p: _append(stream, p))

    await recorder.publish_stream("ORDERS.created", b"js", msg_id="1")
    await recorder.publish("ORDERS.created", b"core")

    assert stream == [b"js"]
    assert core == [b"core"]
    assert [m.jetstream for m in recorder.published()] == [True, False]


async def test_publish_stream_batch_reports_each_message() -> None:
    recorder = natstest.new()

    results = await recorder.publish_stream_batch(
        [
            StreamMsg(subject="ORDERS.a", payload=b"1", msg_id="1", metric_subject="outbox"),
            StreamMsg(subject="ORDERS.b", payload=b"2", msg_id="2", metric_subject="outbox"),
        ]
    )

    assert results == [None, None]
    assert [m.msg_id for m in recorder.published()] == ["1", "2"]
    assert await recorder.publish_stream_batch([]) == []


async def test_deliver_feeds_handlers_without_recording_publication() -> None:
    recorder = natstest.new()
    seen: list[str] = []

    async def handler(_: bytes) -> None:
        seen.append(headers_from_context().get("Event-Type", ""))

    await recorder.subscribe_stream("ORDERS", "orders.>", "worker", handler)
    await recorder.deliver("orders.created", b"{}", headers={"Event-Type": "order.created"})

    assert seen == ["order.created"]
    assert recorder.published() == []


async def test_handler_errors_are_collected_and_do_not_break_publish() -> None:
    recorder = natstest.new()

    async def failing(_: bytes) -> None:
        raise RuntimeError("boom")

    await recorder.subscribe("orders.created", failing)
    await recorder.publish("orders.created", b"x")

    assert [str(e) for e in recorder.handler_errors()] == ["boom"]
    assert len(recorder.published()) == 1


async def test_set_publish_error_switches_bus_to_unavailable() -> None:
    recorder = natstest.new()
    recorder.set_publish_error(RuntimeError("bus down"))

    with pytest.raises(RuntimeError, match="bus down"):
        await recorder.publish("orders.created", b"x")
    assert recorder.published() == []

    recorder.set_publish_error(None)
    await recorder.publish("orders.created", b"x")
    assert len(recorder.published()) == 1


async def test_unsubscribe_and_drain_stop_delivery() -> None:
    recorder = natstest.new()
    hits: list[bytes] = []

    first = await recorder.subscribe("orders.created", lambda p: _append(hits, p))
    second = await recorder.subscribe("orders.created", lambda p: _append(hits, p))
    assert recorder.subscriptions() == ["orders.created", "orders.created"]

    await first.unsubscribe()
    await second.drain(timeout=1)
    await recorder.publish("orders.created", b"x")

    assert hits == []
    assert recorder.subscriptions() == []


async def test_reset_clears_records_but_keeps_subscriptions() -> None:
    recorder = natstest.new()
    hits: list[bytes] = []
    await recorder.subscribe("orders.created", lambda p: _append(hits, p))

    await recorder.publish("orders.created", b"x")
    recorder.reset()
    await recorder.publish("orders.created", b"y")

    assert len(recorder.published()) == 1
    assert hits == [b"x", b"y"]


async def test_typed_helpers_work_against_the_fake() -> None:
    recorder = natstest.new()
    seen: list[dict[str, int]] = []

    async def handler(msg: dict[str, int]) -> None:
        seen.append(msg)

    await subscribe_json(_bus(recorder), "orders.created", handler)
    await publish_json(_bus(recorder), "orders.created", {"id": 1}, msg_id="1")

    assert seen == [{"id": 1}]
    assert recorder.published()[0].payload == b'{"id":1}'

    stream_seen: list[dict[str, int]] = []

    async def stream_handler(msg: dict[str, int]) -> None:
        stream_seen.append(msg)

    await subscribe_stream_json(
        _stream(recorder), "ORDERS", "orders.stream", "worker", stream_handler
    )
    await recorder.publish_stream("orders.stream", b'{"id": 2}')
    assert stream_seen == [{"id": 2}]


async def _append(sink: list[bytes], payload: bytes) -> None:
    sink.append(payload)
