"""Runner бота: polling или webhook по `BOT_MODE`, с отложенной проводкой и graceful-стопом."""

from __future__ import annotations

import redis.asyncio as redis
from aiogram import Bot, Dispatcher
from dishka import AsyncContainer

from pylibinfra import httpkit
from pylibinfra.logkit import Logger
from pylibinfra.natskit import StreamBus
from pylibinfra.tgbotkit.config import Config
from pylibinfra.tgbotkit.extension import NotifierBundle

from .base import BotRunner, _BaseRunner
from .polling import PollingRunner
from .webhook import SECRET_HEADER, WebhookRunner

__all__ = [
    "SECRET_HEADER",
    "BotRunner",
    "PollingRunner",
    "WebhookRunner",
    "build_runner",
]


def build_runner(
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
) -> BotRunner:
    """Runner под `cfg.mode`; `server` обязан быть собран как `HttpProvider(autostart=False)`."""
    runner_cls: type[_BaseRunner] = WebhookRunner if cfg.mode == "webhook" else PollingRunner
    return runner_cls(
        cfg=cfg,
        bot=bot,
        dp=dp,
        container=container,
        client=client,
        server=server,
        log=log,
        notifiers=notifiers,
        bus=bus,
    )
