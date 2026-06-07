"""NotificationRouter — эргономичная точка расширения: router.on(stream, subject,
durable, handler) собирает durable-подписки, а сам router удовлетворяет Notifier
(subscribe регистрирует их через subscribe_stream_json). handler получает
распарсенный payload и Delivery. Альтернатива — реализовать Notifier-протокол
напрямую (если нужна нестандартная проводка).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pylibinfra.natskit import StreamBus, subscribe_stream_json

from .delivery import Delivery

DeliverHandler = Callable[[Any, Delivery], Awaitable[None]]


@dataclass(frozen=True)
class _Route:
    stream: str
    subject: str
    durable: str
    handler: DeliverHandler


def _bind(route: _Route, delivery: Delivery) -> Callable[[Any], Awaitable[None]]:
    async def consume(payload: Any) -> None:
        await route.handler(payload, delivery)

    return consume


class NotificationRouter:
    def __init__(self) -> None:
        self._routes: list[_Route] = []

    def on(self, stream: str, subject: str, durable: str, handler: DeliverHandler) -> None:
        self._routes.append(_Route(stream, subject, durable, handler))

    async def subscribe(self, bus: StreamBus, delivery: Delivery) -> None:
        for route in self._routes:
            await subscribe_stream_json(
                bus, route.stream, route.subject, route.durable, _bind(route, delivery)
            )
