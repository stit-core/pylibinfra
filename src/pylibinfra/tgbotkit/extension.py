"""Точки расширения kit'а — типизированные bundle'ы, которые шаблон наполняет
своим Provider'ом (last-wins), а kit читает как зависимость. Идиома dishka
(как timerkit.list[PeriodicJob] / httpkit.ProbeBundle): не декораторы-с-глобальным-
реестром (теряют DI/тестируемость), не наследование (анти-OCP).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from aiogram import Router

if TYPE_CHECKING:
    from pylibinfra.natskit import StreamBus

    from .notifications.delivery import Delivery

# Dialog является Router (aiogram_dialog) — фиче-диалоги едут сюда же.
RouterBundle = tuple[Router, ...]


@dataclass(frozen=True)
class CommandSpec:
    """Не-диалоговый вход уровня диспетчера: команда, pre_checkout, successful_payment.

    observer — ключ в dp.observers ("message" | "pre_checkout_query" | ...);
    kit регистрирует через dp.observers[observer].register(handler, *filters).
    """

    observer: str
    handler: Callable[..., Any]
    filters: tuple[Any, ...] = ()


CommandBundle = tuple[CommandSpec, ...]


class Notifier(Protocol):
    """Подписчик «событие бэкенда (NATS JetStream) → доставка в Telegram».

    kit вызывает subscribe() на старте (long-running) и даёт shutdown через
    drain соединения. delivery — безопасная обёртка над BgManager (см.
    notifications.delivery): маппинг backend_user_id→chat_id остаётся в шаблоне.
    """

    async def subscribe(self, bus: StreamBus, delivery: Delivery) -> None: ...


NotifierBundle = tuple[Notifier, ...]
