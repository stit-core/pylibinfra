"""Ветки отказов JetStream-пути на фейках: декларация стримов, PubAck, ack/nak/term, pull-цикл."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from nats.aio.client import Client
from nats.js import api
from nats.js.errors import APIError, NotFoundError

from pylibinfra.natskit import (
    HEADER_DLQ_REASON,
    REASON_TERMINAL,
    Config,
    Conn,
    PublishError,
    StreamError,
    StreamMsg,
    TerminalError,
)
from pylibinfra.natskit.jetstream import JsBus, StreamConsumer
from pylibinfra.testkit.logtest import Capture

_URL = "nats://127.0.0.1:4223"


def _cfg(**jetstream: Any) -> Config:
    return Config.model_validate({"url": _URL, "jetstream": {"enabled": True, **jetstream}})


def _bus(js: Any, log: Capture | None = None, **jetstream: Any) -> JsBus:
    cfg = _cfg(**jetstream)
    conn = Conn(Client(), cfg, log or Capture())
    return JsBus(js, conn, cfg, log)


class _FakeJs:
    """Минимальный JetStream-контекст: тест задаёт исходы вызовов, а не поднимает сервер."""

    def __init__(self, **behaviour: Any) -> None:
        self.behaviour = behaviour
        self.created: list[api.StreamConfig] = []
        self.updated: list[api.StreamConfig] = []
        self.published: list[tuple[str, bytes, dict[str, str]]] = []

    async def stream_info(self, name: str) -> Any:
        result = self.behaviour.get("stream_info", NotFoundError())
        if isinstance(result, BaseException):
            raise result
        return result

    async def add_stream(self, config: api.StreamConfig) -> None:
        error = self.behaviour.get("add_stream")
        if isinstance(error, BaseException):
            raise error
        self.created.append(config)

    async def update_stream(self, config: api.StreamConfig) -> None:
        error = self.behaviour.get("update_stream")
        if isinstance(error, BaseException):
            raise error
        self.updated.append(config)

    async def find_stream_name_by_subject(self, subject: str) -> str:
        result = self.behaviour.get("find_stream", NotFoundError())
        if isinstance(result, BaseException):
            raise result
        return str(result)

    async def publish(self, subject: str, payload: bytes, **kwargs: Any) -> None:
        error = self.behaviour.get("publish")
        if isinstance(error, BaseException):
            raise error
        self.published.append((subject, payload, dict(kwargs.get("headers") or {})))

    async def publish_async(self, subject: str, payload: bytes, **kwargs: Any) -> Any:
        result = self.behaviour.get("publish_async")
        if isinstance(result, BaseException):
            raise result
        return result


class _FakeMsg:
    def __init__(
        self, *, deliveries: int = 1, fail: dict[str, BaseException] | None = None
    ) -> None:
        self.subject = "S.evt"
        self.data = b"{}"
        self.headers: dict[str, str] = {}
        self.acked = False
        self.naks: list[float | None] = []
        self.terminated = False
        self.in_progress_calls = 0
        self._fail = fail or {}
        self.metadata = _FakeMetadata(deliveries)

    async def ack(self) -> None:
        if "ack" in self._fail:
            raise self._fail["ack"]
        self.acked = True

    async def nak(self, delay: float | None = None) -> None:
        if "nak" in self._fail:
            raise self._fail["nak"]
        self.naks.append(delay)

    async def term(self) -> None:
        if "term" in self._fail:
            raise self._fail["term"]
        self.terminated = True

    async def in_progress(self) -> None:
        self.in_progress_calls += 1
        if "in_progress" in self._fail:
            raise self._fail["in_progress"]


class _FakeMetadata:
    def __init__(self, deliveries: int) -> None:
        self.num_delivered = deliveries


def _info(config: api.StreamConfig) -> Any:
    return type("Info", (), {"config": config})()


async def test_create_stream_failure_is_stream_error() -> None:
    bus = _bus(_FakeJs(add_stream=RuntimeError("no space")))

    with pytest.raises(StreamError, match="create stream"):
        await bus.ensure_streams(_cfg(stream_names="S"))


async def test_stream_lookup_failure_is_stream_error() -> None:
    bus = _bus(_FakeJs(stream_info=RuntimeError("timeout")))

    with pytest.raises(StreamError, match="stream S"):
        await bus.ensure_streams(_cfg(stream_names="S"))


async def test_creation_race_falls_back_to_subject_union() -> None:
    existing = api.StreamConfig(name="S", subjects=["OTHER.>"])
    race = APIError(err_code=10058, description="stream name already in use")
    js = _FakeJs(add_stream=race, stream_info=_info(existing))
    bus = _bus(js)

    await bus.ensure_streams(_cfg(stream_names="S"))

    assert js.updated[0].subjects == ["OTHER.>", "S.>"]


async def test_update_stream_failure_is_stream_error() -> None:
    existing = api.StreamConfig(name="S", subjects=["OTHER.>"])
    js = _FakeJs(stream_info=_info(existing), update_stream=RuntimeError("locked"))
    bus = _bus(js)

    with pytest.raises(StreamError, match="update stream S"):
        await bus.ensure_streams(_cfg(stream_names="S"))


async def test_existing_stream_with_our_subject_is_left_alone() -> None:
    existing = api.StreamConfig(name="S", subjects=["S.>"], max_age=60.0)
    js = _FakeJs(stream_info=_info(existing))
    bus = _bus(js)

    await bus.ensure_streams(_cfg(stream_names="S", max_age=120.0))

    assert js.updated == []


async def test_empty_stream_names_declare_nothing() -> None:
    js = _FakeJs()
    bus = _bus(js)

    await bus.ensure_streams(_cfg(stream_names=" , "))

    assert js.created == []


async def test_dlq_stream_lookup_failure_is_warned() -> None:
    log = Capture()
    bus = _bus(_FakeJs(find_stream=RuntimeError("api down")), log, dlq_subject="dlq.x")

    await bus.detect_dlq_stream()

    assert log.has("natskit: dlq stream lookup", level="warn")


async def test_dlq_inside_stream_is_reported() -> None:
    log = Capture()
    bus = _bus(_FakeJs(find_stream="DLQ"), log, dlq_subject="dlq.x")

    await bus.detect_dlq_stream()

    entry = log.find("natskit: dlq subject is stored in a stream", level="info")
    assert entry is not None
    assert entry.str("stream") == "DLQ"


async def test_publish_batch_reports_send_failure_and_lost_ack() -> None:
    loop = asyncio.get_running_loop()
    pending: asyncio.Future[Any] = loop.create_future()
    js = _FakeJs(publish_async=pending)
    bus = _bus(js, publish_async_timeout=0.05)

    results = await bus.publish_stream_batch([StreamMsg(subject="S.a", payload=b"1")])

    assert isinstance(results[0], PublishError)
    assert "pub ack timeout" in str(results[0])
    pending.cancel()

    js.behaviour["publish_async"] = RuntimeError("socket closed")
    results = await bus.publish_stream_batch([StreamMsg(subject="S.a", payload=b"1")])
    assert isinstance(results[0], RuntimeError)


async def test_publish_batch_reports_cancelled_and_base_exception() -> None:
    loop = asyncio.get_running_loop()
    cancelled: asyncio.Future[Any] = loop.create_future()
    cancelled.cancel()
    bus = _bus(_FakeJs(publish_async=cancelled), publish_async_timeout=0.05)

    results = await bus.publish_stream_batch([StreamMsg(subject="S.a", payload=b"1")])
    assert isinstance(results[0], PublishError)
    assert "cancelled" in str(results[0])

    broken: asyncio.Future[Any] = loop.create_future()
    broken.set_exception(BaseException("fatal"))
    bus = _bus(_FakeJs(publish_async=broken), publish_async_timeout=0.05)

    results = await bus.publish_stream_batch([StreamMsg(subject="S.a", payload=b"1")])
    assert isinstance(results[0], PublishError)


async def test_publish_stream_failure_is_publish_error() -> None:
    bus = _bus(_FakeJs(publish=RuntimeError("no stream")))

    with pytest.raises(PublishError, match="jetstream publish"):
        await bus.publish_stream("S.evt", b"{}")


async def test_ack_failure_is_logged() -> None:
    log = Capture()
    bus = _bus(_FakeJs(), log)
    msg = _FakeMsg(fail={"ack": RuntimeError("conn closed")})

    await bus._handle_one(_consumer(), msg, _ok_handler)

    assert log.has("natskit: jetstream ack error", level="error")


async def test_nak_failure_is_logged() -> None:
    log = Capture()
    bus = _bus(_FakeJs(), log, max_deliver=5, nak_backoff=0.0)
    msg = _FakeMsg(fail={"nak": RuntimeError("conn closed")})

    await bus._handle_one(_consumer(), msg, _failing_handler)

    assert log.has("natskit: jetstream nak error", level="warn")
    assert log.has("natskit: jetstream handler error", level="error")


async def test_term_failure_is_logged_after_dead_letter() -> None:
    log = Capture()
    bus = _bus(_FakeJs(), log, max_deliver=1, dlq_subject="")
    msg = _FakeMsg(fail={"term": RuntimeError("conn closed")})

    await bus._handle_one(_consumer(), msg, _failing_handler)

    assert log.has("natskit: jetstream message dead", level="error")
    assert log.has("natskit: jetstream term error", level="error")


async def test_panic_handler_goes_through_poison_path() -> None:
    log = Capture()
    bus = _bus(_FakeJs(), log, max_deliver=5, nak_backoff=0.5)
    msg = _FakeMsg()

    async def panicking(_: bytes) -> None:
        raise BaseException("fatal")

    await bus._handle_one(_consumer(), msg, panicking)

    assert msg.naks == [0.5]
    assert log.has("natskit: jetstream handler panic", level="error")


async def test_terminal_error_marks_reason_terminal() -> None:
    js = _FakeJs()
    bus = _bus(js, Capture(), max_deliver=0, dlq_subject="dlq.x")
    msg = _FakeMsg()
    bus._nc = _FakeCore()  # type: ignore[assignment]

    async def terminal(_: bytes) -> None:
        raise TerminalError("natskit: bad schema")

    await bus._handle_one(_consumer(), msg, terminal)

    assert msg.terminated
    sent = bus._nc.published[0]  # type: ignore[attr-defined]
    assert sent[0] == "dlq.x"
    assert sent[2][HEADER_DLQ_REASON] == REASON_TERMINAL


async def test_keep_in_progress_warns_once_on_transient_error() -> None:
    log = Capture()
    bus = _bus(_FakeJs(), log, ack_wait=1.0)
    # Период keepalive — AckWait/2, но конфиг не даёт задать AckWait меньше секунды.
    bus._ack_wait = 0.2
    msg = _FakeMsg(fail={"in_progress": RuntimeError("no responders")})

    task = asyncio.create_task(bus._keep_in_progress(msg, "S.evt"))
    await asyncio.sleep(0.35)
    task.cancel()

    warnings = [e for e in log.entries() if e.msg == "natskit: jetstream in-progress"]
    assert msg.in_progress_calls >= 2
    # Транзиентная ошибка не выключает keepalive, но и не заваливает лог.
    assert len(warnings) == 1


async def test_consume_loop_logs_error_and_stops_on_closed_connection() -> None:
    log = Capture()
    bus = _bus(_FakeJs(), log)
    core = _FakeCore()
    core.is_closed = True
    bus._nc = core  # type: ignore[assignment]
    psub = _FailingPull()

    await bus._consume(psub, _FakeSub(), _consumer(), _ok_handler, asyncio.Event())  # type: ignore[arg-type]

    assert psub.calls == 1
    assert log.has("natskit: jetstream consume error", level="warn")


def _consumer() -> StreamConsumer:
    return StreamConsumer(stream="S", durable="worker", filter="S.evt")


async def _ok_handler(payload: bytes) -> None:
    return None


async def _failing_handler(payload: bytes) -> None:
    raise RuntimeError("boom")


class _FakeCore:
    def __init__(self) -> None:
        self.published: list[tuple[str, bytes, dict[str, str]]] = []
        self.is_closed = False

    async def publish(self, subject: str, payload: bytes, headers: dict[str, str]) -> None:
        self.published.append((subject, payload, headers))

    async def flush(self, timeout: int = 10) -> None:  # noqa: ASYNC109
        return None


class _FakeSub:
    def spawn(self, coro: Any, release: Any) -> None:  # pragma: no cover - не доходим сюда
        coro.close()
        release()


class _FailingPull:
    """Pull-подписка, которая сначала падает, а затем «теряет» соединение."""

    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, batch: int = 1, timeout: float | None = None) -> list[Any]:  # noqa: ASYNC109
        self.calls += 1
        raise RuntimeError("consumer deleted")
