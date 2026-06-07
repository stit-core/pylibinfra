"""BotRunner — общий lifecycle. Deferred-проводка (setup_dishka/setup_dialogs
требуют ГОТОВЫЙ container → не в фабрике, а тут): setup_dishka → setup_dialogs
(строго последним, после include_router) → запуск notifications. На shutdown:
drain → isolation.close → container.close (он же закрывает bot.session/redis/nats
через yield'ы провайдеров).
"""

from __future__ import annotations

from typing import Protocol

import redis.asyncio as redis
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisEventIsolation
from aiogram_dialog import BgManagerFactory, setup_dialogs
from dishka import AsyncContainer
from dishka.integrations.aiogram import setup_dishka

from pylibinfra.logkit import Logger, nop
from pylibinfra.natskit import StreamBus
from pylibinfra.tgbotkit.config import Config
from pylibinfra.tgbotkit.extension import NotifierBundle
from pylibinfra.tgbotkit.notifications.delivery import Delivery
from pylibinfra.tgbotkit.notifications.runner import NotificationRunner
from pylibinfra.tgbotkit.storage import build_isolation


class BotRunner(Protocol):
    async def run(self) -> None: ...


class _BaseRunner:
    def __init__(
        self,
        *,
        cfg: Config,
        bot: Bot,
        dp: Dispatcher,
        container: AsyncContainer,
        client: redis.Redis,
        log: Logger | None = None,
        notifiers: NotifierBundle = (),
        bus: StreamBus | None = None,
    ) -> None:
        self._cfg = cfg
        self._bot = bot
        self._dp = dp
        self._container = container
        self._client = client
        self._log = log or nop()
        self._notifiers = notifiers
        self._bus = bus
        self._isolation: RedisEventIsolation | None = None
        self._notif: NotificationRunner | None = None

    async def _bind(self) -> BgManagerFactory:
        setup_dishka(self._container, self._dp)
        self._isolation = build_isolation(self._client)
        # setup_dialogs — СТРОГО последним (после всех include_router), он же ловит
        # коллизии StatesGroup на старте (smoke).
        return setup_dialogs(self._dp, events_isolation=self._isolation)

    async def _start_notifications(self, bg_factory: BgManagerFactory) -> None:
        if not self._notifiers:
            return
        if self._bus is None:
            self._log.warn(
                "tgbotkit: notifiers registered but no StreamBus provided — skipping. "
                "Add NatsProvider + a StreamBus provider to enable notifications."
            )
            return
        delivery = Delivery(bg_factory, self._bot, self._log)
        self._notif = NotificationRunner(self._bus, delivery, self._notifiers, self._log)
        await self._notif.start()

    async def _cleanup(self) -> None:
        if self._notif is not None:
            await self._notif.drain()
        if self._isolation is not None:
            await self._isolation.close()
        await self._container.close()

    async def run(self) -> None:
        bg_factory = await self._bind()
        await self._start_notifications(bg_factory)
        try:
            await self._serve()
        finally:
            await self._cleanup()

    async def _serve(self) -> None:
        raise NotImplementedError
