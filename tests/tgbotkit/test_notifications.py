"""notifications: маршруты роутера, lifecycle подписок и дедуп доставки."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from pylibinfra.clockkit import Tickable
from pylibinfra.natskit import Subscription
from pylibinfra.testkit.bottest import MemoryRedis
from pylibinfra.testkit.logtest import Capture
from pylibinfra.tgbotkit import (
    Delivery,
    DeliveryDedup,
    NotificationRouter,
    NotificationRunner,
)


class FakeSubscription:
    def __init__(
        self,
        timeout_on_drain: bool = False,
        fail_on_drain: bool = False,
        burn: float = 0.0,
    ) -> None:
        self.drained: list[float | None] = []
        self.unsubscribed = 0
        self._timeout = timeout_on_drain
        self._fail = fail_on_drain
        self._burn = burn

    async def unsubscribe(self) -> None:
        self.unsubscribed += 1

    async def drain(self, *, timeout: float | None = None) -> None:  # noqa: ASYNC109
        self.drained.append(timeout)
        if self._burn:
            await asyncio.sleep(self._burn)
        if self._timeout:
            raise TimeoutError("handlers still running")
        if self._fail:
            raise RuntimeError("connection closed")


class FakeStreamBus:
    """`StreamBus`, запоминающий durable-подписки и умеющий доставить сообщение."""

    def __init__(self, fail_after: int | None = None) -> None:
        self.subscribed: list[tuple[str, str, str]] = []
        self.handlers: dict[str, Any] = {}
        self.opened: list[FakeSubscription] = []
        self._fail_after = fail_after

    async def publish_stream(self, subject: str, payload: bytes, **kwargs: Any) -> None:
        raise AssertionError("not used")

    async def publish_stream_batch(self, msgs: Any) -> list[Exception | None]:
        raise AssertionError("not used")

    async def subscribe_stream(
        self, stream: str, subject: str, durable: str, handler: Any
    ) -> Subscription:
        if self._fail_after is not None and len(self.subscribed) >= self._fail_after:
            raise RuntimeError("consumer already exists")
        self.subscribed.append((stream, subject, durable))
        self.handlers[subject] = handler
        subscription = FakeSubscription()
        self.opened.append(subscription)
        return subscription


def _delivery() -> Delivery:
    return Delivery(None, None)  # type: ignore[arg-type]


async def test_router_opens_a_subscription_per_route() -> None:
    router = NotificationRouter()

    async def handler(payload: Any, delivery: Delivery) -> None: ...

    router.on("ORDERS", "orders.paid", "bot-paid", handler)
    router.on("ORDERS", "orders.refunded", "bot-refunded", handler)
    bus = FakeStreamBus()

    subscriptions = await router.subscribe(bus, _delivery())  # type: ignore[arg-type]
    assert len(subscriptions) == 2
    assert bus.subscribed == [
        ("ORDERS", "orders.paid", "bot-paid"),
        ("ORDERS", "orders.refunded", "bot-refunded"),
    ]


def test_router_keeps_registration_order() -> None:
    router = NotificationRouter()

    async def handler(payload: Any, delivery: Delivery) -> None: ...

    router.on("S", "a", "d-a", handler)
    router.on("S", "b", "d-b", handler)
    assert [route.subject for route in router.routes()] == ["a", "b"]


async def test_router_handler_receives_payload_and_delivery() -> None:
    router = NotificationRouter()
    seen: list[tuple[Any, Delivery]] = []

    async def handler(payload: Any, delivery: Delivery) -> None:
        seen.append((payload, delivery))

    router.on("ORDERS", "orders.paid", "bot", handler)
    bus = FakeStreamBus()
    delivery = _delivery()
    await router.subscribe(bus, delivery)  # type: ignore[arg-type]

    await bus.handlers["orders.paid"](b'{"id": 7}')
    assert seen == [({"id": 7}, delivery)]


class _Notifier:
    def __init__(self, subscriptions: list[FakeSubscription]) -> None:
        self._subscriptions = subscriptions
        self.calls = 0

    async def subscribe(self, bus: Any, delivery: Delivery) -> list[FakeSubscription]:
        self.calls += 1
        return self._subscriptions


async def test_runner_collects_and_drains_subscriptions() -> None:
    subscriptions = [FakeSubscription(), FakeSubscription()]
    notifier = _Notifier(subscriptions)
    runner = NotificationRunner(FakeStreamBus(), _delivery(), (notifier,))  # type: ignore[arg-type]

    await runner.start()
    assert notifier.calls == 1

    await runner.drain(2.5)
    budgets = [s.drained[0] for s in subscriptions]
    assert all(b is not None for b in budgets)
    # Бюджет один на весь дренаж: вторая подписка получает остаток, а не полный таймаут.
    assert budgets[0] <= 2.5  # type: ignore[operator]
    assert budgets[1] <= budgets[0]  # type: ignore[operator]


async def test_runner_drain_budget_is_spent_by_the_first_subscription() -> None:
    slow = FakeSubscription(burn=0.05)
    rest = FakeSubscription()
    runner = NotificationRunner(FakeStreamBus(), _delivery(), (_Notifier([slow, rest]),))  # type: ignore[arg-type]

    await runner.start()
    await runner.drain(0.05)
    # Бюджет выбран первой подпиской: второй остаётся ноль, а не ещё столько же —
    # иначе остановка растянулась бы на «таймаут × число подписок».
    budget = slow.drained[0]
    assert budget is not None
    assert 0.0 < budget <= 0.05
    assert rest.drained == [0.0]


async def test_runner_drain_without_a_deadline_waits_indefinitely() -> None:
    subscription = FakeSubscription()
    runner = NotificationRunner(FakeStreamBus(), _delivery(), (_Notifier([subscription]),))  # type: ignore[arg-type]

    await runner.start()
    await runner.drain()
    assert subscription.drained == [None]


async def test_runner_drain_is_idempotent() -> None:
    subscription = FakeSubscription()
    runner = NotificationRunner(FakeStreamBus(), _delivery(), (_Notifier([subscription]),))  # type: ignore[arg-type]

    await runner.start()
    await runner.drain(1.0)
    await runner.drain(1.0)
    assert len(subscription.drained) == 1


async def test_runner_logs_drain_timeout() -> None:
    log = Capture()
    subscription = FakeSubscription(timeout_on_drain=True)
    runner = NotificationRunner(
        FakeStreamBus(),  # type: ignore[arg-type]
        _delivery(),
        (_Notifier([subscription]),),
        log,
    )

    await runner.start()
    await runner.drain(0.1)
    assert log.has("tgbotkit: notification drain timed out", level="warn")


async def test_router_unsubscribes_what_it_opened_before_a_failure() -> None:
    router = NotificationRouter()

    async def handler(payload: Any, delivery: Delivery) -> None: ...

    router.on("S", "a", "d-a", handler)
    router.on("S", "b", "d-b", handler)
    bus = FakeStreamBus(fail_after=1)

    with pytest.raises(RuntimeError, match="already exists"):
        await router.subscribe(bus, _delivery())  # type: ignore[arg-type]
    assert [s.unsubscribed for s in bus.opened] == [1]


async def test_runner_unsubscribes_opened_notifiers_when_a_later_one_fails() -> None:
    opened = FakeSubscription()

    class _Broken:
        async def subscribe(self, bus: Any, delivery: Delivery) -> list[FakeSubscription]:
            raise RuntimeError("stream is gone")

    runner = NotificationRunner(
        FakeStreamBus(),  # type: ignore[arg-type]
        _delivery(),
        (_Notifier([opened]), _Broken()),
    )
    with pytest.raises(RuntimeError, match="stream is gone"):
        await runner.start()
    assert opened.unsubscribed == 1

    await runner.drain(1.0)
    assert opened.drained == []


async def test_runner_logs_a_failed_rollback_and_keeps_going() -> None:
    log = Capture()

    class _Stubborn(FakeSubscription):
        async def unsubscribe(self) -> None:
            await super().unsubscribe()
            raise RuntimeError("connection closed")

    stubborn = _Stubborn()
    healthy = FakeSubscription()

    class _Broken:
        async def subscribe(self, bus: Any, delivery: Delivery) -> list[FakeSubscription]:
            raise RuntimeError("stream is gone")

    runner = NotificationRunner(
        FakeStreamBus(),  # type: ignore[arg-type]
        _delivery(),
        (_Notifier([stubborn, healthy]), _Broken()),
        log,
    )
    with pytest.raises(RuntimeError, match="stream is gone"):
        await runner.start()

    # Упавший откат не имеет права оставить следующие подписки висеть на стриме.
    assert healthy.unsubscribed == 1
    assert log.has("tgbotkit: notification unsubscribe failed", level="error")


async def test_runner_drains_the_rest_after_a_broken_subscription() -> None:
    log = Capture()
    broken = FakeSubscription(fail_on_drain=True)
    healthy = FakeSubscription()
    runner = NotificationRunner(
        FakeStreamBus(),  # type: ignore[arg-type]
        _delivery(),
        (_Notifier([broken, healthy]),),
        log,
    )

    await runner.start()
    await runner.drain(1.0)
    assert len(healthy.drained) == 1
    assert log.has("tgbotkit: notification drain failed", level="error")


async def test_runner_without_notifiers_does_nothing() -> None:
    runner = NotificationRunner(FakeStreamBus(), _delivery(), ())  # type: ignore[arg-type]
    await runner.start()
    await runner.drain(1.0)


async def test_delivery_dedup_reserve_commit_release(redis_client: MemoryRedis) -> None:
    dedup = DeliveryDedup(redis_client)  # type: ignore[arg-type]
    token = await dedup.reserve("order-1")
    assert token is not None
    assert await dedup.reserve("order-1") is None

    assert await dedup.release("order-1", token) is True
    again = await dedup.reserve("order-1")
    assert again is not None

    assert await dedup.commit("order-1", again) is True
    assert await dedup.reserve("order-1") is None


async def test_delivery_dedup_release_of_a_foreign_reservation_is_refused() -> None:
    clock = Tickable(datetime(2026, 1, 1, tzinfo=UTC))
    client = MemoryRedis(clock=clock)
    stale = await DeliveryDedup(client, reserve_ttl=60.0).reserve("order-1")  # type: ignore[arg-type]
    assert stale is not None

    clock.advance(61.0)
    fresh = DeliveryDedup(client)  # type: ignore[arg-type]
    assert await fresh.reserve("order-1") is not None

    # Опоздавший NAK не имеет права открыть ключ, который уже держит другой воркер.
    assert await fresh.release("order-1", stale) is False
    assert await fresh.reserve("order-1") is None
