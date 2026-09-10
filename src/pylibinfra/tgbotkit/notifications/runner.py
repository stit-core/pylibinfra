"""NotificationRunner — lifecycle подписок «событие бэкенда → доставка в Telegram»."""

from __future__ import annotations

import time

from pylibinfra.logkit import Logger, nop
from pylibinfra.natskit import StreamBus, Subscription
from pylibinfra.tgbotkit.extension import Notifier

from .delivery import Delivery


class NotificationRunner:
    """Поднимает подписки всех `Notifier`'ов на старте и дренит их на остановке."""

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
        self._log = log if log is not None else nop()
        self._subscriptions: tuple[Subscription, ...] = ()

    async def start(self) -> None:
        """Открывает подписки; сбой любого `Notifier`'а валит старт бота.

        Уже открытые подписки при сбое снимаются здесь же: иначе они продолжили бы
        тянуть сообщения из JetStream в процесс, который так и не поднялся.
        """
        opened: list[Subscription] = []
        try:
            for notifier in self._notifiers:
                opened.extend(await notifier.subscribe(self._bus, self._delivery))
        except BaseException:
            await self._unsubscribe(opened)
            raise
        self._subscriptions = tuple(opened)
        self._log.info(
            "tgbotkit: notifications started",
            notifiers=len(self._notifiers),
            subscriptions=len(self._subscriptions),
        )

    async def drain(self, timeout: float | None = None) -> None:  # noqa: ASYNC109
        """Снимает подписки и ждёт обработчики в общий бюджет `timeout`; исчерпан — в лог.

        Бюджет один на весь дренаж, а не на каждую подписку: десять подписок с
        персональным дедлайном растянули бы остановку на десять `BOT_SHUTDOWN_TIMEOUT`
        и процесс добивал бы SIGKILL оркестратора. Дренит только свои handle'ы:
        соединение закрывает `NatsProvider` на закрытии контейнера, поэтому дважды
        его не гасим. Сбой одной подписки не отменяет дренаж остальных — иначе они
        пережили бы остановку бота.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        for subscription in self._subscriptions:
            budget = None if deadline is None else max(deadline - time.monotonic(), 0.0)
            try:
                await subscription.drain(timeout=budget)
            except TimeoutError:
                self._log.warn("tgbotkit: notification drain timed out", timeout=budget)
            except Exception as exc:
                self._log.error("tgbotkit: notification drain failed", err=repr(exc))
        self._subscriptions = ()

    async def _unsubscribe(self, opened: list[Subscription]) -> None:
        for subscription in opened:
            try:
                await subscription.unsubscribe()
            except Exception as exc:
                self._log.error("tgbotkit: notification unsubscribe failed", err=repr(exc))
