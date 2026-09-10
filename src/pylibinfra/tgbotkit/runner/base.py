"""Общий lifecycle бота: отложенная проводка, фоновые подписки, остановка по дедлайну."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable
from typing import Any, Protocol

import redis.asyncio as redis
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisEventIsolation
from aiogram_dialog import BgManagerFactory, setup_dialogs
from dishka import AsyncContainer
from dishka.integrations.aiogram import setup_dishka

from pylibinfra import httpkit
from pylibinfra.logkit import Logger, nop
from pylibinfra.natskit import StreamBus
from pylibinfra.tgbotkit.config import Config
from pylibinfra.tgbotkit.errors import RunnerError
from pylibinfra.tgbotkit.extension import NotifierBundle
from pylibinfra.tgbotkit.i18n import build_translator
from pylibinfra.tgbotkit.notifications.delivery import Delivery
from pylibinfra.tgbotkit.notifications.runner import NotificationRunner
from pylibinfra.tgbotkit.storage import build_isolation


class BotRunner(Protocol):
    """Порт запуска бота: `run` держит процесс до остановки, `stop` её запрашивает."""

    async def run(self) -> None:
        """Поднимает бота, обслуживает апдейты и гасит всё на выходе."""

    def stop(self) -> None:
        """Просит завершиться — то же, что SIGINT/SIGTERM; `run` дойдёт до конца сам."""


class _BaseRunner:
    """Общая часть polling- и webhook-режимов: проводка, уведомления, остановка."""

    def __init__(
        self,
        *,
        cfg: Config,
        bot: Bot,
        dp: Dispatcher,
        container: AsyncContainer,
        client: redis.Redis,
        server: httpkit.Server,
        log: Logger | None = None,
        notifiers: NotifierBundle = (),
        bus: StreamBus | None = None,
    ) -> None:
        self._cfg = cfg
        self._bot = bot
        self._dp = dp
        self._container = container
        self._client = client
        self._server = server
        self._log = log if log is not None else nop()
        self._notifiers = notifiers
        self._bus = bus
        self._isolation: RedisEventIsolation | None = None
        self._notifications: NotificationRunner | None = None
        self._stopped = asyncio.Event()
        self._started = False

    def stop(self) -> None:
        """Просит завершиться — то же, что SIGINT/SIGTERM; ждать остановки нужно `run`."""
        self._stopped.set()

    async def run(self) -> None:
        """Собирает проводку, поднимает подписки и обслуживает апдейты до остановки.

        Одноразовый: `run` закрывает контейнер на выходе, поэтому повторный вызов
        отвергается `RunnerError` вместо тихой работы на закрытом графе.
        """
        if self._server.autostart:
            raise RunnerError(
                "tgbotkit: runner owns the HTTP server lifecycle — "
                "register httpkit.HttpProvider(autostart=False)"
            )
        if self._started:
            raise RunnerError("tgbotkit: runner is single-use — build a new one to run again")
        self._started = True
        try:
            await self._start_notifications(await self._bind())
            await self._serve()
        finally:
            # Сорванный старт тоже обязан закрыть контейнер: иначе процесс падает,
            # не отпустив соединения с Redis, NATS и Telegram.
            await self._shutdown()

    async def _serve(self) -> None:
        raise NotImplementedError

    async def _bind(self) -> BgManagerFactory:
        """Проводка, требующая готового контейнера, поэтому отложенная до `run`."""
        setup_dishka(self._container, self._dp)
        self._isolation = build_isolation(self._client)
        # setup_dialogs строго последним — после всех include_router: он же ловит
        # коллизии StatesGroup на старте.
        return setup_dialogs(self._dp, events_isolation=self._isolation)

    async def _start_notifications(self, bg_factory: BgManagerFactory) -> None:
        if not self._notifiers:
            return
        if self._bus is None:
            self._log.warn(
                "tgbotkit: notifiers registered without a StreamBus, skipping subscriptions"
            )
            return
        # Фон идёт вне dp.update, где I18nMiddleware не отрабатывает, поэтому у
        # Delivery свой translator.
        delivery = Delivery(
            bg_factory,
            self._bot,
            self._log,
            build_translator(self._cfg.i18n),
            self._cfg.i18n.default_locale,
        )
        self._notifications = NotificationRunner(self._bus, delivery, self._notifiers, self._log)
        await self._notifications.start()

    async def _shutdown(self) -> None:
        if self._notifications is not None:
            await self._step("notifications", self._notifications.drain(self._cfg.shutdown_timeout))
        if self._isolation is not None:
            await self._step("isolation", self._isolation.close())
        await self._step("container", self._container.close())

    async def _step(self, name: str, action: Awaitable[Any]) -> None:
        """Шаг остановки: сбой одного не имеет права оставить незакрытыми остальные."""
        try:
            await action
        except Exception as exc:
            self._log.error("tgbotkit: shutdown step failed", step=name, err=repr(exc))

    async def _serve_probes(self) -> asyncio.Task[None]:
        """Занимает порт синхронно, чтобы занятый адрес свалил старт, а не первую пробу."""
        self._server.listen()
        self._log.info("tgbotkit: http listening", addr=self._server.addr())
        task = asyncio.create_task(self._server.serve())
        await asyncio.sleep(0)
        return task

    async def _stop_http(self, task: asyncio.Task[None]) -> None:
        await self._server.stop()
        with contextlib.suppress(asyncio.CancelledError):
            await task
