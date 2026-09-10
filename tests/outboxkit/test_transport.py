"""Transport поверх JetStream: namespaced `Nats-Msg-Id` и конечный label publish-метрик."""

from __future__ import annotations

from pylibinfra.outboxkit import DEFAULT_INSTANCE, OutMsg, new_stream_transport
from pylibinfra.testkit import natstest


async def test_default_transport_uses_bare_row_id() -> None:
    recorder = natstest.new()
    transport = new_stream_transport(recorder)

    await transport.publish(OutMsg(id=42, subject="order.created", payload=b"{}"))

    published = recorder.published()[0]
    assert published.msg_id == "42"
    # Доменный subject в label'е publish-метрик дал бы неограниченную кардинальность.
    assert published.metric_subject == DEFAULT_INSTANCE
    assert published.jetstream is True


async def test_namespaced_transport_prefixes_msg_id() -> None:
    recorder = natstest.new()
    orders = new_stream_transport(recorder, "orders")
    leads = new_stream_transport(recorder, "leads")

    results = await orders.publish_batch(
        [
            OutMsg(id=1, subject="order.created", payload=b"1"),
            OutMsg(id=2, subject="order.created", payload=b"2"),
        ]
    )
    await leads.publish(OutMsg(id=1, subject="lead.created", payload=b"3"))

    assert results == [None, None]
    ids = [m.msg_id for m in recorder.published()]
    # Один id в двух namespace'ах даёт разные Msg-Id: иначе дедуп уронил бы второй факт.
    assert ids == ["orders:1", "orders:2", "leads:1"]
    assert {m.metric_subject for m in recorder.published()} == {"orders", "leads"}


async def test_batch_failure_is_reported_per_message() -> None:
    recorder = natstest.new()
    recorder.set_publish_error(RuntimeError("bus down"))
    transport = new_stream_transport(recorder, "orders")

    results = await transport.publish_batch([OutMsg(id=1, subject="order.created", payload=b"1")])

    assert isinstance(results[0], RuntimeError)
