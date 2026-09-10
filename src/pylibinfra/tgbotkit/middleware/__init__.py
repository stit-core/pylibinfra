"""Цепочка `dp.update`: request-id, dedup, throttle, recovery, observability."""

from __future__ import annotations

import redis.asyncio as redis
from aiogram import Dispatcher

from pylibinfra.logkit import Logger
from pylibinfra.tgbotkit.config import Config
from pylibinfra.tgbotkit.errorux import ErrorUX
from pylibinfra.tgbotkit.metrics import Instruments

from .dedup import DedupMiddleware
from .flood import GCRA_LUA, OutgoingThrottleMiddleware
from .observability import SPAN_NAME, ObservabilityMiddleware, update_type
from .recovery import RecoveryMiddleware
from .request_id import RequestIdMiddleware, chat_id, register_log_attrs, update_id, user_id
from .throttle import INCR_WINDOW_LUA, ThrottleMiddleware

__all__ = [
    "GCRA_LUA",
    "INCR_WINDOW_LUA",
    "SPAN_NAME",
    "DedupMiddleware",
    "ObservabilityMiddleware",
    "OutgoingThrottleMiddleware",
    "RecoveryMiddleware",
    "RequestIdMiddleware",
    "ThrottleMiddleware",
    "chat_id",
    "install_standard_middleware",
    "register_log_attrs",
    "update_id",
    "update_type",
    "user_id",
]


def install_standard_middleware(
    dp: Dispatcher,
    cfg: Config,
    log: Logger,
    instruments: Instruments,
    client: redis.Redis,
    ux: ErrorUX,
) -> None:
    """Вешает цепочку кита на `dp.update` — единственный вход и обычных, и фоновых событий."""
    dp.update.outer_middleware(RequestIdMiddleware())
    if cfg.dedup_enabled:
        dp.update.outer_middleware(DedupMiddleware(client, cfg.dedup_ttl, instruments, log))
    if cfg.throttle_rps > 0:
        dp.update.outer_middleware(ThrottleMiddleware(client, cfg.throttle_rps, instruments, log))
    dp.update.outer_middleware(RecoveryMiddleware(ux, instruments, log))
    dp.update.outer_middleware(ObservabilityMiddleware(instruments))
