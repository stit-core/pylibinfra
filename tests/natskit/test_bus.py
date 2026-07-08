from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from pylibinfra.natskit import (
    Config,
    Conn,
    NatsBus,
    publish_json,
    subscribe_json,
)


class FakeMsg:
    def __init__(self, subject: str, data: bytes, headers: dict[str, str]) -> None:
        self.subject = subject
        self.data = data
        self.headers = headers


class FakeClient:
    """Записывает publish'ы и хранит подписки; deliver() прогоняет cb по
    записанным сообщениям синхронно в текущем event-loop'е."""

    def __init__(self) -> None:
        self.is_connected = True
        self.published: list[tuple[str, bytes, dict[str, str]]] = []
        self.queues: dict[str, str] = {}
        self._subs: dict[str, Callable[[Any], Awaitable[None]]] = {}

    async def publish(
        self, subject: str, payload: bytes = b"", headers: dict[str, str] | None = None
    ) -> None:
        self.published.append((subject, payload, headers or {}))

    async def subscribe(
        self, subject: str, cb: Callable[[Any], Awaitable[None]], queue: str = ""
    ) -> None:
        self._subs[subject] = cb
        if queue:
            self.queues[subject] = queue

    async def deliver(
        self, subject: str, data: bytes, headers: dict[str, str] | None = None
    ) -> None:
        await self._subs[subject](FakeMsg(subject, data, headers or {}))


def fake_conn() -> tuple[Conn, FakeClient]:
    fc = FakeClient()
    return Conn(fc), fc  # type: ignore[arg-type]


async def drain_handlers(bus: NatsBus) -> None:
    """Дожидается завершения spawned handler-тасок (cb обрабатывает сообщение
    в отдельной таске под семафором)."""
    while bus._tasks:
        await asyncio.gather(*list(bus._tasks))


async def test_publish_json_serializes_and_sends() -> None:
    conn, fc = fake_conn()
    bus = NatsBus(conn)
    await publish_json(bus, "test.json", {"hello": "world"})

    assert len(fc.published) == 1
    subject, payload, _ = fc.published[0]
    assert subject == "test.json"
    assert payload == b'{"hello":"world"}'


async def test_subscribe_json_deserializes() -> None:
    conn, fc = fake_conn()
    bus = NatsBus(conn)

    got: asyncio.Future[dict[str, int]] = asyncio.get_event_loop().create_future()

    async def handler(msg: dict[str, int]) -> None:
        got.set_result(msg)

    await subscribe_json(bus, "test.json", handler)
    await fc.deliver("test.json", json.dumps({"value": 42}).encode())
    await drain_handlers(bus)

    assert got.result() == {"value": 42}


async def test_panic_recovery_does_not_crash() -> None:
    conn, fc = fake_conn()
    bus = NatsBus(conn)

    after: asyncio.Future[bytes] = asyncio.get_event_loop().create_future()

    async def handler(payload: bytes) -> None:
        if payload == b'"boom"':
            raise RuntimeError("boom")
        after.set_result(payload)

    await bus.subscribe("test.panic", handler)
    await fc.deliver("test.panic", json.dumps("boom").encode())
    await fc.deliver("test.panic", json.dumps("ok").encode())
    await drain_handlers(bus)

    # Сбой первого handler'а не помешал доставке второго сообщения.
    assert after.result() == b'"ok"'


async def test_backpressure_caps_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    conn, fc = fake_conn()
    monkeypatch.setenv("NATS_MAX_IN_FLIGHT", "3")
    cfg = Config()
    assert cfg.backpressure.max_in_flight == 3
    bus = NatsBus(conn, cfg=cfg)

    current = 0
    peak = 0
    release = asyncio.Event()
    done = 0

    async def handler(_: bytes) -> None:
        nonlocal current, peak, done
        current += 1
        peak = max(peak, current)
        await release.wait()
        current -= 1
        done += 1

    await bus.subscribe("test.bp", handler)
    # Доставляем конкурентно: cb блокируется на sem.acquire() при заполнении —
    # последовательный await завис бы на 4-й доставке.
    deliveries = [asyncio.create_task(fc.deliver("test.bp", b"x")) for _ in range(20)]

    # Семафор (=3) держит остальные cb заблокированными на acquire(), пока
    # release не отпустит первые три, поэтому peak не превышает лимит.
    await asyncio.sleep(0.05)
    assert peak <= 3
    release.set()
    await asyncio.gather(*deliveries)
    await drain_handlers(bus)
    assert done == 20


async def test_publish_injects_trace_headers() -> None:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    trace.set_tracer_provider(TracerProvider())
    conn, fc = fake_conn()
    bus = NatsBus(conn)

    await bus.publish("test.trace", {"k": "v"})

    _, _, headers = fc.published[0]
    assert "traceparent" in headers


async def test_subscribe_queue_passes_queue_group() -> None:
    conn, fc = fake_conn()
    bus = NatsBus(conn)

    got: asyncio.Future[bytes] = asyncio.get_event_loop().create_future()

    async def handler(payload: bytes) -> None:
        got.set_result(payload)

    await bus.subscribe_queue("test.q", "workers", handler)
    assert fc.queues.get("test.q") == "workers"

    await fc.deliver("test.q", b"x")
    await drain_handlers(bus)
    assert got.result() == b"x"


async def test_subject_and_headers_in_context() -> None:
    from pylibinfra.natskit import headers_from_context, subject_from_context

    conn, fc = fake_conn()
    bus = NatsBus(conn)

    seen: asyncio.Future[tuple[str, dict[str, str]]] = asyncio.get_event_loop().create_future()

    async def handler(_: bytes) -> None:
        seen.set_result((subject_from_context(), headers_from_context()))

    await bus.subscribe("a.b.cmd", handler)
    await fc.deliver("a.b.cmd", b"{}", {"event-type": "created"})
    await drain_handlers(bus)

    subject, headers = seen.result()
    assert subject == "a.b.cmd"
    assert headers.get("event-type") == "created"
    # Вне обработчика контекст пуст.
    assert subject_from_context() == ""
    assert headers_from_context() == {}
