"""Core NATS против живого сервера: доставка, queue-группы, backpressure, drain и сигналы."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest
import pytest_asyncio
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from pylibinfra.natskit import (
    Bus,
    ClosedError,
    Conn,
    ConnectionClosedError,
    NatsBus,
    headers_from_context,
    new_conn,
    probe,
    publish_json,
    subject_from_context,
    subscribe_json,
    subscribe_queue_json,
)
from pylibinfra.testkit import NatsFixture
from pylibinfra.testkit.logtest import Capture

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_WAIT = 5.0


@pytest_asyncio.fixture(loop_scope="module")
async def bus_factory(
    nats_server: NatsFixture,
) -> AsyncIterator[Callable[..., AsyncIterator[tuple[Conn, Bus]]]]:
    conns: list[Conn] = []

    async def make(log: Capture | None = None, **overrides: object) -> tuple[Conn, Bus]:
        cfg = nats_server.config(**overrides)
        conn = await new_conn(cfg, log)
        conns.append(conn)
        return conn, NatsBus(conn, cfg, log)

    yield make  # type: ignore[misc]
    for conn in conns:
        await conn.close(timeout=2)


async def test_publish_subscribe_roundtrip(bus_factory: Callable[..., object]) -> None:
    _, bus = await bus_factory()  # type: ignore[misc]
    got: asyncio.Future[tuple[dict[str, int], str, dict[str, str]]] = (
        asyncio.get_running_loop().create_future()
    )

    async def handler(msg: dict[str, int]) -> None:
        if not got.done():
            got.set_result((msg, subject_from_context(), headers_from_context()))

    await subscribe_json(bus, "orders.*.created", handler)
    await publish_json(
        bus, "orders.42.created", {"id": 42}, headers={"Event-Type": "order.created"}
    )

    payload, subject, headers = await asyncio.wait_for(got, _WAIT)
    assert payload == {"id": 42}
    # Wildcard-подписчик читает конкретный subject из контекста, а не из payload.
    assert subject == "orders.42.created"
    assert headers["Event-Type"] == "order.created"


async def test_queue_group_load_balances(bus_factory: Callable[..., object]) -> None:
    _, bus = await bus_factory()  # type: ignore[misc]
    seen: list[int] = []
    done = asyncio.Event()

    async def handler(msg: dict[str, int]) -> None:
        seen.append(msg["id"])
        if len(seen) == 6:
            done.set()

    for _ in range(3):
        await subscribe_queue_json(bus, "queue.work", "workers", handler)
    for i in range(6):
        await publish_json(bus, "queue.work", {"id": i})

    await asyncio.wait_for(done.wait(), _WAIT)
    await asyncio.sleep(0.2)
    # Queue-группа: каждое сообщение ровно одному подписчику, без дублей.
    assert sorted(seen) == [0, 1, 2, 3, 4, 5]


async def test_handler_error_is_logged_and_subscription_survives(
    bus_factory: Callable[..., object],
) -> None:
    log = Capture()
    _, bus = await bus_factory(log)  # type: ignore[misc]
    delivered: list[bytes] = []
    second = asyncio.Event()

    async def handler(payload: bytes) -> None:
        delivered.append(payload)
        if payload == b"first":
            raise RuntimeError("boom")
        second.set()

    await bus.subscribe("errors.core", handler)
    await bus.publish("errors.core", b"first")
    await bus.publish("errors.core", b"second")

    await asyncio.wait_for(second.wait(), _WAIT)
    assert delivered == [b"first", b"second"]
    entry = log.find("natskit: handler error", level="error")
    assert entry is not None
    assert entry.str("subject") == "errors.core"
    assert entry.str("instance") == "default"


async def test_backpressure_caps_concurrent_handlers(bus_factory: Callable[..., object]) -> None:
    _, bus = await bus_factory(None, backpressure={"max_in_flight": 2})  # type: ignore[misc]
    running = 0
    peak = 0
    done = asyncio.Event()
    handled = 0

    async def handler(_: bytes) -> None:
        nonlocal running, peak, handled
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1
        handled += 1
        if handled == 8:
            done.set()

    await bus.subscribe("backpressure.core", handler)
    for _ in range(8):
        await bus.publish("backpressure.core", b"x")

    await asyncio.wait_for(done.wait(), _WAIT)
    assert peak <= 2


async def test_handler_timeout_cancels_stuck_handler(bus_factory: Callable[..., object]) -> None:
    log = Capture()
    _, bus = await bus_factory(log, handler_timeout=0.2)  # type: ignore[misc]
    cancelled = asyncio.Event()

    async def handler(_: bytes) -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    await bus.subscribe("timeout.core", handler)
    await bus.publish("timeout.core", b"x")

    await asyncio.wait_for(cancelled.wait(), _WAIT)
    await asyncio.sleep(0.1)
    entry = log.find("natskit: handler error", level="error")
    assert entry is not None
    assert "handler timeout" in entry.str("err")


async def test_subscription_unsubscribe_stops_delivery(bus_factory: Callable[..., object]) -> None:
    _, bus = await bus_factory()  # type: ignore[misc]
    delivered: list[bytes] = []
    first = asyncio.Event()

    async def handler(payload: bytes) -> None:
        delivered.append(payload)
        first.set()

    sub = await bus.subscribe("unsub.core", handler)
    await bus.publish("unsub.core", b"first")
    await asyncio.wait_for(first.wait(), _WAIT)

    await sub.unsubscribe()
    await bus.publish("unsub.core", b"second")
    await asyncio.sleep(0.3)

    assert delivered == [b"first"]


async def test_subscription_drain_waits_for_running_handler(
    bus_factory: Callable[..., object],
) -> None:
    _, bus = await bus_factory()  # type: ignore[misc]
    started = asyncio.Event()
    finished = asyncio.Event()

    async def handler(_: bytes) -> None:
        started.set()
        await asyncio.sleep(0.3)
        finished.set()

    sub = await bus.subscribe("drain.core", handler)
    await bus.publish("drain.core", b"x")
    await asyncio.wait_for(started.wait(), _WAIT)

    await sub.drain(timeout=_WAIT)

    assert finished.is_set()
    # Повторные drain/unsubscribe идемпотентны: транспорт останавливается ровно раз.
    await sub.drain(timeout=_WAIT)
    await sub.unsubscribe()


async def test_close_waits_for_handler_and_rejects_new_subscriptions(
    nats_server: NatsFixture,
) -> None:
    log = Capture()
    conn = await new_conn(nats_server.config(), log)
    bus = NatsBus(conn, nats_server.config(), log)
    finished = asyncio.Event()
    started = asyncio.Event()

    async def handler(_: bytes) -> None:
        started.set()
        await asyncio.sleep(0.3)
        finished.set()

    await bus.subscribe("close.core", handler)
    ready = probe(conn)
    await ready.run()
    await bus.publish("close.core", b"x")
    await asyncio.wait_for(started.wait(), _WAIT)

    await conn.close()

    assert finished.is_set()
    assert conn.status() == "closed"
    assert ready.name == "natskit"
    with pytest.raises(ConnectionClosedError):
        conn.healthy()
    with pytest.raises(ClosedError):
        await bus.subscribe("close.core", handler)
    # Повторный close идемпотентен.
    await conn.close()


async def test_close_gives_up_on_stuck_handler_after_drain_timeout(
    nats_server: NatsFixture,
) -> None:
    log = Capture()
    cfg = nats_server.config(drain_timeout=0.3)
    conn = await new_conn(cfg, log)
    bus = NatsBus(conn, cfg, log)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(_: bytes) -> None:
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    await bus.subscribe("stuck.core", handler)
    await bus.publish("stuck.core", b"x")
    await asyncio.wait_for(started.wait(), _WAIT)

    loop = asyncio.get_running_loop()
    start = loop.time()
    await conn.close()
    elapsed = loop.time() - start

    assert elapsed < 3.0
    assert cancelled.is_set()
    assert log.has("natskit: drain timeout, handlers still in flight", level="warn")


async def test_emits_metrics_and_linked_spans(
    nats_server: NatsFixture,
    read_metric: Callable[..., float],
    span_exporter: InMemorySpanExporter,
) -> None:
    cfg = nats_server.config()
    conn = await new_conn(cfg)
    bus = NatsBus(conn, cfg)
    done = asyncio.Event()

    async def handler(_: bytes) -> None:
        done.set()

    try:
        await bus.subscribe("signals.*", handler)
        await bus.publish("signals.one", b"x", metric_subject="signals.*")
        await bus.publish("signals.two", b"y")
        await asyncio.wait_for(done.wait(), _WAIT)
        await asyncio.sleep(0.2)
    finally:
        await conn.close()

    assert read_metric("nats_messages_published_total", {"status": "ok"}) == 2
    # Label subject на publish-метриках — только с metric_subject.
    assert read_metric("nats_messages_published_total", {"subject": "signals.*"}) == 1
    assert read_metric("nats_publish_duration_seconds") == 2
    assert (
        read_metric(
            "nats_messages_consumed_total",
            {"subject": "signals.*", "status": "ok", "instance": "default"},
        )
        >= 1
    )
    assert read_metric("nats_handle_duration_seconds", {"subject": "signals.*"}) >= 1
    assert read_metric("nats_inflight_messages", {"subject": "signals.*"}) == 0

    spans = {span.name: span for span in span_exporter.get_finished_spans()}
    producer = spans["natskit.publish signals.*"]
    consumer = spans["natskit.handle signals.*"]
    assert producer.attributes is not None
    assert producer.attributes["messaging.destination.name"] == "signals.one"
    assert consumer.attributes is not None
    assert consumer.attributes["messaging.destination.name"] in ("signals.one", "signals.two")
    # Consumer-спан — child producer'а: trace-context доехал в заголовках сообщения.
    linked = [
        span
        for span in span_exporter.get_finished_spans()
        if span.name == "natskit.handle signals.*" and span.parent is not None
    ]
    assert linked
    assert linked[0].parent is not None
    assert linked[0].context is not None
    assert linked[0].context.trace_id in {
        span.context.trace_id
        for span in span_exporter.get_finished_spans()
        if span.name.startswith("natskit.publish") and span.context is not None
    }
