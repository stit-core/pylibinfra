"""Runner: polling | webhook по cfg.mode, с deferred-проводкой и graceful-shutdown."""

from __future__ import annotations

import redis.asyncio as redis
from aiogram import Bot, Dispatcher
from dishka import AsyncContainer

from pylibinfra.logkit import Logger
from pylibinfra.natskit import Conn, StreamBus
from pylibinfra.tgbotkit.config import Config
from pylibinfra.tgbotkit.extension import NotifierBundle

from .base import BotRunner, _BaseRunner
from .polling import PollingRunner
from .webhook import WebhookRunner

__all__ = ["BotRunner", "PollingRunner", "WebhookRunner", "build_runner"]


def build_runner(
    *,
    cfg: Config,
    bot: Bot,
    dp: Dispatcher,
    container: AsyncContainer,
    client: redis.Redis,
    log: Logger | None = None,
    notifiers: NotifierBundle = (),
    bus: StreamBus | None = None,
    conn: Conn | None = None,
) -> BotRunner:
    runner_cls: type[_BaseRunner] = WebhookRunner if cfg.mode == "webhook" else PollingRunner
    return runner_cls(
        cfg=cfg,
        bot=bot,
        dp=dp,
        container=container,
        client=client,
        log=log,
        notifiers=notifiers,
        bus=bus,
        conn=conn,
    )
