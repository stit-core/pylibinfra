"""Ветки отказов Core-пути и сборки клиента на фейках, без живого сервера."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from nats.aio.client import Client

from pylibinfra.natskit import (
    Config,
    Conn,
    NatsBus,
    PublishError,
    StreamError,
    SubscribeError,
    new_client,
)
from pylibinfra.natskit.bus import invoke_handler
from pylibinfra.testkit.logtest import Capture

_URL = "nats://127.0.0.1:4223"


def _conn(raw: Any, log: Capture, cfg: Config | None = None) -> Conn:
    conn = Conn(Client(), cfg or Config(url=_URL), log)
    conn._nc = raw
    return conn


class _BrokenClient:
    def __init__(self) -> None:
        self.is_closed = False

    async def publish(self, subject: str, payload: bytes, headers: dict[str, str]) -> None:
        raise RuntimeError("connection closed")

    async def subscribe(self, subject: str, queue: str = "", cb: Any = None) -> Any:
        raise RuntimeError("invalid subject")


class _FakeMsg:
    def __init__(self) -> None:
        self.subject = "core.evt"
        self.data = b"{}"
        self.headers: dict[str, str] = {}


class _StuckSubscription:
    """Подписка nats-py, чей drain не расходится: delivery-цикл встал на семафоре."""

    async def drain(self) -> None:
        await asyncio.Event().wait()

    async def unsubscribe(self) -> None:
        return None


class _StuckClient:
    """Клиент, у которого дренится только соединение, но не подписка."""

    def __init__(self) -> None:
        self.is_closed = False
        self.drained = False

    async def subscribe(self, subject: str, queue: str = "", cb: Any = None) -> Any:
        return _StuckSubscription()

    async def drain(self) -> None:
        self.drained = True

    async def close(self) -> None:
        self.is_closed = True


async def test_publish_failure_is_publish_error(read_metric: Any) -> None:
    log = Capture()
    bus = NatsBus(_conn(_BrokenClient(), log), Config(url=_URL), log)

    with pytest.raises(PublishError, match="natskit: publish"):
        await bus.publish("core.evt", b"x", metric_subject="core.*")

    assert read_metric("nats_messages_published_total", {"status": "error"}) == 1


async def test_subscribe_failure_is_subscribe_error() -> None:
    log = Capture()
    bus = NatsBus(_conn(_BrokenClient(), log), Config(url=_URL), log)

    with pytest.raises(SubscribeError, match="natskit: subscribe"):
        await bus.subscribe("core.>", _noop)


async def test_handler_panic_is_recovered_and_logged(read_metric: Any) -> None:
    log = Capture()
    bus = NatsBus(_conn(_BrokenClient(), log), Config(url=_URL), log)

    async def panicking(_: bytes) -> None:
        raise BaseException("fatal")

    await bus._handle_one(_FakeMsg(), "core.evt", panicking)

    assert log.has("natskit: handler panic", level="error")
    assert read_metric("nats_messages_consumed_total", {"status": "panic"}) == 1


async def test_handler_cancellation_propagates() -> None:
    log = Capture()
    bus = NatsBus(_conn(_BrokenClient(), log), Config(url=_URL), log)

    async def cancelling(_: bytes) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await bus._handle_one(_FakeMsg(), "core.evt", cancelling)
    # Отмена — это остановка сервиса, а не ошибка обработчика: лога нет.
    assert not log.has("natskit: handler error", level="error")


async def test_zero_timeout_disables_handler_deadline() -> None:
    seen: list[bytes] = []

    async def handler(payload: bytes) -> None:
        await asyncio.sleep(0.05)
        seen.append(payload)

    await invoke_handler(handler, b"x", 0)

    assert seen == [b"x"]


async def test_close_is_bounded_when_subscription_drain_hangs() -> None:
    log = Capture()
    raw = _StuckClient()
    cfg = Config(url=_URL, drain_timeout=0.2)
    conn = _conn(raw, log, cfg)
    bus = NatsBus(conn, cfg, log)
    await bus.subscribe("core.evt", _noop)

    loop = asyncio.get_running_loop()
    start = loop.time()
    await conn.close()

    assert loop.time() - start < 3.0
    assert raw.drained
    entry = log.find("natskit: subscription drain", level="warn")
    assert entry is not None
    assert "drain timeout" in entry.str("err")
    # Обработчиков не было — жалобы на них и отмены быть не должно.
    assert not log.has("natskit: drain timeout, handlers still in flight")


async def test_drain_timeout_leaves_subscription_to_close() -> None:
    log = Capture()
    raw = _StuckClient()
    cfg = Config(url=_URL, drain_timeout=0.2)
    conn = _conn(raw, log, cfg)
    bus = NatsBus(conn, cfg, log)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def stuck(_: bytes) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    sub = await bus.subscribe("core.evt", stuck)
    sub.spawn(bus._handle_one(_FakeMsg(), "core.evt", stuck), lambda: None)
    await asyncio.wait_for(started.wait(), 5)

    with pytest.raises(TimeoutError):
        await sub.drain(timeout=0.05)

    # Подписка осталась в реестре: отменить зависший обработчик может только close().
    await conn.close()
    assert cancelled.is_set()


async def test_new_client_closes_connection_when_streams_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pylibinfra.natskit import client as client_module

    closed = asyncio.Event()

    class _Conn:
        instance = "default"
        log = Capture()

        async def close(self, *, timeout: float | None = None) -> None:  # noqa: ASYNC109
            closed.set()

    async def fake_new_conn(*args: Any, **kwargs: Any) -> Any:
        return _Conn()

    async def failing_stream_bus(*args: Any, **kwargs: Any) -> Any:
        raise StreamError("natskit: stream ORDERS is unmanaged and unavailable")

    monkeypatch.setattr(client_module, "new_conn", fake_new_conn)
    monkeypatch.setattr(client_module, "new_stream_bus", failing_stream_bus)

    with pytest.raises(StreamError):
        await new_client("", Config(url=_URL))

    assert closed.is_set()


async def _noop(payload: bytes) -> None:
    return None
