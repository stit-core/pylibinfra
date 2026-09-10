"""JetStream против живого сервера: персист, дедуп, poison-путь (DLQ/term/NAK) и стримы."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest
import pytest_asyncio
from nats.js import api

from pylibinfra.natskit import (
    HEADER_DLQ_CONSUMER,
    HEADER_DLQ_DELIVERIES,
    HEADER_DLQ_ERROR,
    HEADER_DLQ_INSTANCE,
    HEADER_DLQ_MSG_ID,
    HEADER_DLQ_ORIGIN,
    HEADER_DLQ_REASON,
    HEADER_DLQ_STREAM,
    MSG_ID_HEADER,
    REASON_MAX_DELIVER,
    REASON_TERMINAL,
    ConfigError,
    Conn,
    DisabledStreamBus,
    JetStreamDisabledError,
    NatsBus,
    PublishError,
    StreamBus,
    StreamError,
    StreamMsg,
    TerminalError,
    headers_from_context,
    new_conn,
    new_stream_bus,
    publish_stream_json,
    subject_from_context,
    subscribe_stream_json,
)
from pylibinfra.testkit import NatsFixture
from pylibinfra.testkit.logtest import Capture

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_WAIT = 20.0


@pytest_asyncio.fixture(loop_scope="module")
async def stream_factory(nats_server: NatsFixture) -> AsyncIterator[Callable[..., object]]:
    conns: list[Conn] = []

    async def make(
        log: Capture | None = None, **jetstream: object
    ) -> tuple[Conn, StreamBus, NatsBus]:
        cfg = nats_server.config(jetstream={"enabled": True, **jetstream})
        conn = await new_conn(cfg, log)
        conns.append(conn)
        return conn, await new_stream_bus(conn, cfg, log), NatsBus(conn, cfg, log)

    yield make
    for conn in conns:
        await conn.close(timeout=3)


async def _dlq_sink(bus: NatsBus, subject: str) -> asyncio.Queue[dict[str, str]]:
    """Core-подписка на DLQ-subject: тест ловит копии мёртвых сообщений."""
    sink: asyncio.Queue[dict[str, str]] = asyncio.Queue()

    async def handler(_: bytes) -> None:
        await sink.put(headers_from_context())

    await bus.subscribe(subject, handler)
    return sink


async def test_publish_subscribe_roundtrip(stream_factory: Callable[..., object]) -> None:
    _, sb, _ = await stream_factory(None, stream_names="RT", max_age=3600.0)  # type: ignore[misc]
    got: asyncio.Future[tuple[dict[str, int], str, dict[str, str]]] = (
        asyncio.get_running_loop().create_future()
    )

    async def handler(msg: dict[str, int]) -> None:
        if not got.done():
            got.set_result((msg, subject_from_context(), headers_from_context()))

    await subscribe_stream_json(sb, "RT", "RT.*.created", "rt-worker", handler)
    await publish_stream_json(
        sb, "RT.7.created", {"id": 7}, msg_id="rt-7", headers={"Event-Type": "created"}
    )

    payload, subject, headers = await asyncio.wait_for(got, _WAIT)
    assert payload == {"id": 7}
    assert subject == "RT.7.created"
    assert headers["Event-Type"] == "created"
    assert headers[MSG_ID_HEADER] == "rt-7"


async def test_publish_batch_dedups_and_reports_per_message(
    stream_factory: Callable[..., object],
) -> None:
    conn, sb, _ = await stream_factory(  # type: ignore[misc]
        None, stream_names="BATCH", max_age=3600.0, publish_async_timeout=2.0
    )

    results = await sb.publish_stream_batch(
        [
            StreamMsg(subject="BATCH.a", payload=b"1", msg_id="dup", metric_subject="outbox"),
            StreamMsg(subject="BATCH.a", payload=b"2", msg_id="dup", metric_subject="outbox"),
            StreamMsg(subject="NOSTREAM.a", payload=b"3", msg_id="x"),
        ]
    )

    assert results[0] is None
    # Дубль по Msg-Id — это PubAck, а не ошибка: стрим просто не хранит его дважды.
    assert results[1] is None
    assert results[2] is not None
    info = await conn.raw.jetstream().stream_info("BATCH")
    assert info.state.messages == 1
    assert await sb.publish_stream_batch([]) == []


async def test_publish_stream_reports_missing_stream(
    stream_factory: Callable[..., object],
) -> None:
    _, sb, _ = await stream_factory(None, stream_names="MISS", publish_async_timeout=2.0)  # type: ignore[misc]

    with pytest.raises(PublishError, match="jetstream publish"):
        await sb.publish_stream("NOSTREAM.evt", b"x")


async def test_max_deliver_sends_copy_to_dlq_and_terminates(
    stream_factory: Callable[..., object],
) -> None:
    log = Capture()
    _, sb, bus = await stream_factory(  # type: ignore[misc]
        log,
        stream_names="POISON",
        max_age=3600.0,
        ack_wait=1.0,
        max_deliver=2,
        nak_backoff=0.2,
        dlq_subject="dlq.poison",
    )
    sink = await _dlq_sink(bus, "dlq.poison")
    attempts = 0

    async def failing(_: bytes) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("boom")

    await sb.subscribe_stream("POISON", "POISON.evt", "poison-worker", failing)
    await sb.publish_stream("POISON.evt", b'{"n":1}', msg_id="poison-1")

    headers = await asyncio.wait_for(sink.get(), _WAIT)
    await asyncio.sleep(1.5)

    assert attempts == 2
    assert headers[HEADER_DLQ_ORIGIN] == "POISON.evt"
    assert headers[HEADER_DLQ_REASON] == REASON_MAX_DELIVER
    assert headers[HEADER_DLQ_ERROR] == "boom"
    assert headers[HEADER_DLQ_STREAM] == "POISON"
    assert headers[HEADER_DLQ_CONSUMER] == "poison-worker"
    assert headers[HEADER_DLQ_DELIVERIES] == "2"
    assert headers[HEADER_DLQ_INSTANCE] == "default"
    # Nats-Msg-Id в копию не переносится: стрим с дедупом выбросил бы её как дубль.
    assert MSG_ID_HEADER not in headers
    assert headers[HEADER_DLQ_MSG_ID] == "poison-1"
    assert log.has("natskit: jetstream message dead", level="error")


async def test_terminal_error_goes_to_dlq_on_first_attempt(
    stream_factory: Callable[..., object],
) -> None:
    _, sb, bus = await stream_factory(  # type: ignore[misc]
        None,
        stream_names="TERM",
        max_age=3600.0,
        ack_wait=1.0,
        max_deliver=5,
        nak_backoff=0.2,
        dlq_subject="dlq.term",
    )
    sink = await _dlq_sink(bus, "dlq.term")
    calls = 0

    async def terminal(_: bytes) -> None:
        nonlocal calls
        calls += 1
        raise TerminalError("natskit: unknown schema")

    await sb.subscribe_stream("TERM", "TERM.evt", "term-worker", terminal)
    await sb.publish_stream("TERM.evt", b"{}")

    headers = await asyncio.wait_for(sink.get(), _WAIT)
    await asyncio.sleep(1.0)

    assert calls == 1
    assert headers[HEADER_DLQ_REASON] == REASON_TERMINAL
    assert headers[HEADER_DLQ_DELIVERIES] == "1"


async def test_decode_error_is_terminal(stream_factory: Callable[..., object]) -> None:
    _, sb, bus = await stream_factory(  # type: ignore[misc]
        None,
        stream_names="DECODE",
        max_age=3600.0,
        ack_wait=1.0,
        max_deliver=5,
        nak_backoff=0.2,
        dlq_subject="dlq.decode",
    )
    sink = await _dlq_sink(bus, "dlq.decode")
    calls = 0

    async def handler(_: dict[str, int]) -> None:
        nonlocal calls
        calls += 1

    await subscribe_stream_json(sb, "DECODE", "DECODE.evt", "decode-worker", handler)
    await sb.publish_stream("DECODE.evt", b"{not json")

    headers = await asyncio.wait_for(sink.get(), _WAIT)

    assert calls == 0
    assert headers[HEADER_DLQ_REASON] == REASON_TERMINAL


async def test_dlq_publish_failure_keeps_message_for_redelivery(
    nats_server: NatsFixture,
    read_metric: Callable[..., float],
) -> None:
    log = Capture()
    cfg = nats_server.config(
        jetstream={
            "enabled": True,
            "stream_names": "KEEP",
            "max_age": 3600.0,
            "ack_wait": 1.0,
            "max_deliver": 2,
            "nak_backoff": 0.2,
            "dlq_subject": "dlqfull.keep",
        }
    )
    conn = await new_conn(cfg, log)
    js = conn.raw.jetstream()
    # DLQ-стрим отвергает копию: max_msgs=1 + discard=new и он уже полон.
    await js.add_stream(
        config=api.StreamConfig(
            name="DLQFULL",
            subjects=["dlqfull.>"],
            max_msgs=1,
            discard=api.DiscardPolicy.NEW,
            storage=api.StorageType.MEMORY,
        )
    )
    await js.publish("dlqfull.keep", b"filler")

    try:
        sb = await new_stream_bus(conn, cfg, log)
        attempts = 0
        second = asyncio.Event()

        async def failing(_: bytes) -> None:
            nonlocal attempts
            attempts += 1
            if attempts >= 2:
                second.set()
            raise RuntimeError("boom")

        await sb.subscribe_stream("KEEP", "KEEP.evt", "keep-worker", failing)
        await sb.publish_stream("KEEP.evt", b"{}")

        await asyncio.wait_for(second.wait(), _WAIT)
        await asyncio.sleep(1.0)

        assert log.has(
            "natskit: jetstream dlq publish failed, message kept for redelivery", level="error"
        )
        assert not log.has("natskit: jetstream message dead", level="error")
        assert read_metric("nats_messages_consumed_total", {"status": "dlq_error"}) >= 1
    finally:
        await conn.close(timeout=3)


async def test_nak_backoff_delays_redelivery(stream_factory: Callable[..., object]) -> None:
    _, sb, _ = await stream_factory(  # type: ignore[misc]
        None, stream_names="BACKOFF", max_age=3600.0, ack_wait=5.0, max_deliver=2, nak_backoff=1.5
    )
    stamps: list[float] = []
    second = asyncio.Event()
    loop = asyncio.get_running_loop()

    async def failing(_: bytes) -> None:
        stamps.append(loop.time())
        if len(stamps) == 2:
            second.set()
        raise RuntimeError("boom")

    await sb.subscribe_stream("BACKOFF", "BACKOFF.evt", "backoff-worker", failing)
    await sb.publish_stream("BACKOFF.evt", b"{}")

    await asyncio.wait_for(second.wait(), _WAIT)

    # Backoff разносит попытки во времени, а не крутит горячую петлю.
    assert stamps[1] - stamps[0] >= 1.0


async def test_slow_handler_is_not_redelivered(stream_factory: Callable[..., object]) -> None:
    _, sb, _ = await stream_factory(  # type: ignore[misc]
        None,
        stream_names="SLOW",
        max_age=3600.0,
        ack_wait=1.0,
        handler_timeout=10.0,
        max_deliver=5,
        nak_backoff=0.2,
    )
    calls = 0
    done = asyncio.Event()

    async def slow(_: bytes) -> None:
        nonlocal calls
        calls += 1
        await asyncio.sleep(2.5)
        done.set()

    await sb.subscribe_stream("SLOW", "SLOW.evt", "slow-worker", slow)
    await sb.publish_stream("SLOW.evt", b"{}")

    await asyncio.wait_for(done.wait(), _WAIT)
    await asyncio.sleep(1.5)

    # in_progress продлевает ожидание сервера, пока обработчик жив.
    assert calls == 1


async def test_stuck_handler_is_cancelled_and_message_redelivered(
    stream_factory: Callable[..., object],
) -> None:
    _, sb, _ = await stream_factory(  # type: ignore[misc]
        None,
        stream_names="STUCK",
        max_age=3600.0,
        ack_wait=1.0,
        handler_timeout=1.0,
        max_deliver=2,
        nak_backoff=0.2,
    )
    calls = 0
    cancelled = asyncio.Event()
    second = asyncio.Event()

    async def stuck(_: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            second.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    await sb.subscribe_stream("STUCK", "STUCK.evt", "stuck-worker", stuck)
    await sb.publish_stream("STUCK.evt", b"{}")

    await asyncio.wait_for(second.wait(), _WAIT)

    assert cancelled.is_set()
    assert calls == 2


async def test_ensure_streams_creates_missing_and_keeps_operator_tuning(
    nats_server: NatsFixture,
) -> None:
    log = Capture()
    conn = await new_conn(nats_server.config(), log)
    try:
        js = conn.raw.jetstream()
        await js.add_stream(
            config=api.StreamConfig(
                name="TUNED",
                subjects=["EXTRA.>"],
                max_age=3600.0,
                storage=api.StorageType.MEMORY,
            )
        )

        cfg = nats_server.config(
            jetstream={"enabled": True, "stream_names": "TUNED,FRESH", "max_age": 7200.0}
        )
        await new_stream_bus(conn, cfg, log)

        tuned = (await js.stream_info("TUNED")).config
        fresh = (await js.stream_info("FRESH")).config
        # Кит владеет только своим subject'ом: остальное — зона оператора.
        assert set(tuned.subjects or []) == {"EXTRA.>", "TUNED.>"}
        assert tuned.max_age == 3600.0
        assert tuned.storage == api.StorageType.MEMORY
        assert fresh.subjects == ["FRESH.>"]
        assert fresh.max_age == 7200.0
        drift = log.find("natskit: stream config drift", level="warn")
        assert drift is not None
        assert drift.str("stream") == "TUNED"
    finally:
        await conn.close(timeout=3)


async def test_unmanaged_streams_must_exist(nats_server: NatsFixture) -> None:
    cfg = nats_server.config(
        jetstream={"enabled": True, "stream_names": "ABSENT", "stream_managed": False}
    )
    conn = await new_conn(cfg)
    try:
        with pytest.raises(StreamError, match="unmanaged and unavailable"):
            await new_stream_bus(conn, cfg)
    finally:
        await conn.close(timeout=3)


async def test_disabled_jetstream_returns_stub(nats_server: NatsFixture) -> None:
    cfg = nats_server.config()
    conn = await new_conn(cfg)
    try:
        sb = await new_stream_bus(conn, cfg)

        assert isinstance(sb, DisabledStreamBus)
        with pytest.raises(JetStreamDisabledError):
            await sb.publish_stream("x", b"1")
        with pytest.raises(JetStreamDisabledError):
            await sb.publish_stream_batch([StreamMsg(subject="x", payload=b"1")])
        with pytest.raises(JetStreamDisabledError):
            await sb.subscribe_stream("S", "x", "d", _noop)
    finally:
        await conn.close(timeout=3)


async def test_subscribe_stream_rejects_filter_covering_dlq(
    stream_factory: Callable[..., object],
) -> None:
    _, sb, _ = await stream_factory(  # type: ignore[misc]
        None, stream_names="LOOP", max_age=3600.0, dlq_subject="LOOP.dlq"
    )

    with pytest.raises(ConfigError, match="dead letters would loop back"):
        await sb.subscribe_stream("LOOP", "LOOP.>", "loop-worker", _noop)


async def test_consume_metrics_use_filter_subject(
    stream_factory: Callable[..., object],
    read_metric: Callable[..., float],
) -> None:
    _, sb, _ = await stream_factory(None, stream_names="METRICS", max_age=3600.0)  # type: ignore[misc]
    done = asyncio.Event()

    async def handler(_: bytes) -> None:
        done.set()

    await sb.subscribe_stream("METRICS", "METRICS.*", "metrics-worker", handler)
    await sb.publish_stream("METRICS.one", b"{}", metric_subject="metrics")
    await asyncio.wait_for(done.wait(), _WAIT)
    await asyncio.sleep(0.3)

    assert (
        read_metric(
            "nats_messages_consumed_total",
            {"subject": "METRICS.*", "status": "ok", "transport": "jetstream"},
        )
        >= 1
    )
    assert (
        read_metric(
            "nats_messages_published_total", {"subject": "metrics", "transport": "jetstream"}
        )
        >= 1
    )
    assert read_metric("nats_inflight_messages", {"subject": "METRICS.*"}) == 0


async def test_stream_subscription_drain_and_unsubscribe(
    stream_factory: Callable[..., object],
) -> None:
    _, sb, _ = await stream_factory(None, stream_names="DRAIN", max_age=3600.0, ack_wait=2.0)  # type: ignore[misc]
    handled: list[bytes] = []
    started = asyncio.Event()

    async def handler(payload: bytes) -> None:
        started.set()
        await asyncio.sleep(0.3)
        handled.append(payload)

    sub = await sb.subscribe_stream("DRAIN", "DRAIN.evt", "drain-worker", handler)
    await sb.publish_stream("DRAIN.evt", b"first")
    await asyncio.wait_for(started.wait(), _WAIT)

    await sub.drain(timeout=_WAIT)
    assert handled == [b"first"]

    second = await sb.subscribe_stream("DRAIN", "DRAIN.other", "other-worker", handler)
    await second.unsubscribe()


async def _noop(payload: bytes) -> None:
    return None
