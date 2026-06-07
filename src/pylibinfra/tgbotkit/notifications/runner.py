"""NotificationRunner — на старте бота поднимает все Notifier'ы (durable
JetStream-подписки), на shutdown — drain: bus.drain() отписывает консьюмеры и
дожидается in-flight handler'ов (ack/nak до закрытия conn). Само закрытие
соединения (nc.drain) — при container.close() (NatsProvider).
"""

from __future__ import annotations

from pylibinfra.logkit import Logger, nop
from pylibinfra.natskit import StreamBus
from pylibinfra.tgbotkit.extension import Notifier

from .delivery import Delivery


class NotificationRunner:
    def __init__(
        self,
        bus: StreamBus,
        delivery: Delivery,
        notifiers: tuple[Notifier, ...],
        log: Logger | None = None,
    ) -> None:
        self._bus = bus
        self._delivery = delivery
        self._notifiers = notifiers
        self._log = log or nop()

    async def start(self) -> None:
        for notifier in self._notifiers:
            await notifier.subscribe(self._bus, self._delivery)
        self._log.info("tgbotkit: notifications started", count=len(self._notifiers))

    async def drain(self) -> None:
        self._log.info("tgbotkit: notifications draining")
        await self._bus.drain()
