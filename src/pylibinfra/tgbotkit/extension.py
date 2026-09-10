"""Точки расширения кита: кортежи-ключи DI, которые наполняет App-Provider консьюмера."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from aiogram import Router

if TYPE_CHECKING:  # pragma: no cover
    from pylibinfra.natskit import StreamBus, Subscription

    from .notifications.delivery import Delivery

RouterBundle = tuple[Router, ...]
"""DI-ключ роутеров и диалогов сервиса; `Dialog` тоже `Router`, поэтому едет сюда же."""


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """Вход уровня диспетчера: команда, `pre_checkout_query`, `successful_payment`.

    `observer` — ключ в `dp.observers`; кит регистрирует обработчик как
    `dp.observers[observer].register(handler, *filters)`.
    """

    observer: str
    handler: Callable[..., Any]
    filters: tuple[Any, ...] = ()


CommandBundle = tuple[CommandSpec, ...]
"""DI-ключ не-диалоговых входов диспетчера."""


class Notifier(Protocol):
    """Порт подписки «событие бэкенда → доставка в Telegram»."""

    async def subscribe(self, bus: StreamBus, delivery: Delivery) -> Sequence[Subscription]:
        """Открывает свои подписки и возвращает их handle'ы — кит дренит их на остановке."""


NotifierBundle = tuple[Notifier, ...]
"""DI-ключ фоновых подписчиков; пустой кортеж — бот работает без NATS."""
