"""OutgoingThrottleMiddleware (session-level, на bot.session): покрывает ВСЕ
исходящие вызовы (диалог-рендеры И доставку через BgManager). Закрывает три
дыры разом:

* H2 rate-limit — Telegram режет ~30 msg/s глобально и ~1 msg/s в чат. GCRA-
  token-bucket в общем redis (atomic Lua) считает «сколько спать до свободного
  токена» отдельно для global и per-chat ключа; middleware ждёт (sleep), а не
  дропает — это доставка, терять нельзя.
* H5 per-chat ordering — burst параллельных send в один чат (из JetStream) мог
  переставиться. In-process per-chat asyncio.Lock сериализует вызовы в один chat_id.
* H2 429 — aiogram 3.28 сам TelegramRetryAfter НЕ ретраит: ловим, спим
  e.retry_after, повторяем до retry_after_attempts попыток.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import redis.asyncio as redis
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.exceptions import TelegramRetryAfter

from pylibinfra.logkit import Logger, nop
from pylibinfra.tgbotkit.config import Config

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.methods import Response, TelegramMethod
    from aiogram.methods.base import TelegramType

# Верхняя граница суммарного ожидания одного вызова в GCRA-цикле: на доставке
# лучше упереться и залогировать, чем повиснуть навечно при забитом bucket'е.
_MAX_TOTAL_WAIT = 60.0
# Потолок словаря per-chat локов: at-most-once cleanup отпущенных локов при росте.
_LOCKS_SOFT_CAP = 10_000

# GCRA (Generic Cell Rate Algorithm): emission interval = 1/rate секунд.
# В ключе храним TAT (theoretical arrival time, мс). Если now >= TAT — токен есть
# сразу, сдвигаем TAT на интервал, возвращаем 0. Иначе возвращаем (TAT - now) —
# сколько спать до свободного токена. Burst в 1 (без накопления) — строгая
# равномерность под лимит. Атомарность Lua гарантирует корректность при N воркерах.
_GCRA = (
    "local interval = tonumber(ARGV[1])\n"
    "local now = tonumber(ARGV[2])\n"
    "local tat = tonumber(redis.call('GET', KEYS[1]) or now)\n"
    "if tat < now then tat = now end\n"
    "local wait = tat - now\n"
    "redis.call('SET', KEYS[1], tat + interval, 'PX', math.ceil(interval) + 1000)\n"
    "return wait\n"
)


class OutgoingThrottleMiddleware(BaseRequestMiddleware):
    def __init__(self, client: redis.Redis, cfg: Config, log: Logger | None = None) -> None:
        self._redis = client
        self._log = log or nop()
        self._global_interval = self._interval(cfg.rate_global)
        self._chat_interval = self._interval(cfg.rate_per_chat)
        self._attempts = max(cfg.retry_after_attempts, 1)
        self._gcra = client.register_script(_GCRA)
        self._locks: dict[int, asyncio.Lock] = {}

    @staticmethod
    def _interval(rate: float) -> float:
        # msg/s → emission interval в мс; rate<=0 трактуем как «без лимита» (0).
        return 1000.0 / rate if rate > 0 else 0.0

    def _lock_for(self, chat_id: int) -> asyncio.Lock:
        lock = self._locks.get(chat_id)
        if lock is None:
            if len(self._locks) >= _LOCKS_SOFT_CAP:
                # Чистим только свободные локи — занятые держат in-flight send.
                self._locks = {cid: lk for cid, lk in self._locks.items() if lk.locked()}
            lock = self._locks.setdefault(chat_id, asyncio.Lock())
        return lock

    async def _throttle(self, key: str, interval: float) -> None:
        if interval <= 0:
            return
        # GCRA уже зарезервировал слот этого вызова и вернул время до него: ждём
        # ОДИН раз и идём. Повторный запрос сдвинул бы TAT ещё на interval — под
        # burst'ом (N конкурентных send) это бесконечная гонка до cap'а.
        wait_ms = float(await self._gcra(keys=[key], args=[interval, time.time() * 1000.0]))
        wait_s = wait_ms / 1000.0
        if wait_s <= 0:
            return
        if wait_s > _MAX_TOTAL_WAIT:
            self._log.warn("tgbotkit: rate-limit wait cap hit, sending anyway", key=key)
            return
        await asyncio.sleep(wait_s)

    async def _gate(self, chat_id: int | None) -> None:
        await self._throttle("tg:rate:global", self._global_interval)
        if chat_id is not None:
            await self._throttle(f"tg:rate:chat:{chat_id}", self._chat_interval)

    async def _send(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        await self._gate(getattr(method, "chat_id", None))
        for attempt in range(1, self._attempts + 1):
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as e:
                if attempt == self._attempts:
                    raise
                self._log.warn(
                    "tgbotkit: 429 retry_after, backing off",
                    retry_after=e.retry_after,
                    attempt=attempt,
                )
                await asyncio.sleep(e.retry_after)
        raise AssertionError("unreachable")  # цикл всегда return'ит или raise'ит

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        chat_id = getattr(method, "chat_id", None)
        if chat_id is None:
            return await self._send(make_request, bot, method)
        # Per-chat single-flight: rate-gate + send под одним локом, чтобы порядок
        # сообщений в чате сохранялся даже под burst'ом параллельных send.
        async with self._lock_for(chat_id):
            return await self._send(make_request, bot, method)
