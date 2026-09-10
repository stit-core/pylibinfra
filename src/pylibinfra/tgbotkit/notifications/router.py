"""NotificationRouter — сахар над `Notifier`: маршруты «subject → обработчик доставки»."""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pylibinfra.natskit import StreamBus, Subscription, subscribe_stream_json

from .delivery import Delivery

DeliverHandler = Callable[[Any, Delivery], Awaitable[None]]
"""Обработчик события: разобранный payload и `Delivery`; исключение из него — повтор доставки."""


@dataclass(frozen=True, slots=True)
class Route:
    """Durable-подписка на subject стрима и обработчик её сообщений."""

    stream: str
    subject: str
    durable: str
    handler: DeliverHandler


class NotificationRouter:
    """Собирает durable-подписки декларативно и сам удовлетворяет протокол `Notifier`."""

    def __init__(self) -> None:
        self._routes: list[Route] = []

    def on(self, stream: str, subject: str, durable: str, handler: DeliverHandler) -> None:
        """Регистрирует маршрут; подписки открываются на старте бота, а не здесь."""
        self._routes.append(Route(stream, subject, durable, handler))

    def routes(self) -> tuple[Route, ...]:
        """Зарегистрированные маршруты в порядке добавления."""
        return tuple(self._routes)

    async def subscribe(self, bus: StreamBus, delivery: Delivery) -> tuple[Subscription, ...]:
        """Открывает подписки маршрутов; их handle'ы дренит `NotificationRunner` на остановке.

        Сбой на середине снимает уже открытые подписки: иначе часть маршрутов осталась
        бы висеть на стриме после провала старта.
        """
        opened: list[Subscription] = []
        try:
            for route in self._routes:
                opened.append(
                    await subscribe_stream_json(
                        bus, route.stream, route.subject, route.durable, _bind(route, delivery)
                    )
                )
        except BaseException:
            for subscription in opened:
                with contextlib.suppress(Exception):
                    await subscription.unsubscribe()
            raise
        return tuple(opened)


def _bind(route: Route, delivery: Delivery) -> Callable[[Any], Awaitable[None]]:
    async def consume(payload: Any) -> None:
        await route.handler(payload, delivery)

    return consume
