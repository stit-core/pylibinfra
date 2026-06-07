"""Стандартная outer-цепочка на dp.update (единая точка: обычные И фоновые
dialog-апдейты идут через propagate_event("update")). Порядок регистрации =
снаружи→внутрь: request_id → dedup → throttle → recovery → observability.
"""

from __future__ import annotations

import redis.asyncio as redis
from aiogram import Dispatcher

from pylibinfra.logkit import Logger
from pylibinfra.tgbotkit.config import Config
from pylibinfra.tgbotkit.errorux import ErrorUX
from pylibinfra.tgbotkit.metrics import TgInstruments

from .dedup import DedupMiddleware
from .observability import ObservabilityMiddleware
from .recovery import RecoveryMiddleware
from .request_id import RequestIdMiddleware
from .throttle import ThrottleMiddleware

__all__ = [
    "DedupMiddleware",
    "ObservabilityMiddleware",
    "RecoveryMiddleware",
    "RequestIdMiddleware",
    "ThrottleMiddleware",
    "install_standard_middleware",
]


def install_standard_middleware(
    dp: Dispatcher,
    cfg: Config,
    log: Logger,
    instruments: TgInstruments,
    client: redis.Redis,
    ux: ErrorUX,
) -> None:
    dp.update.outer_middleware(RequestIdMiddleware())
    if cfg.dedup_enabled:
        dp.update.outer_middleware(DedupMiddleware(client, cfg.dedup_ttl, instruments, log))
    if cfg.throttle_rps > 0:
        dp.update.outer_middleware(ThrottleMiddleware(client, cfg.throttle_rps))
    dp.update.outer_middleware(RecoveryMiddleware(ux, instruments, log))
    dp.update.outer_middleware(ObservabilityMiddleware(instruments))
