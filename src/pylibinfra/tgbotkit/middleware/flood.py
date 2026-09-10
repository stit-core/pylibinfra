"""OutgoingThrottleMiddleware: rate-limit, порядок сообщений в чате и ретрай `429`."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import redis.asyncio as redis
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.exceptions import TelegramRetryAfter
from redis.exceptions import RedisError

from pylibinfra.clockkit import Clock, Real
from pylibinfra.logkit import Logger, nop
from pylibinfra.tgbotkit.config import Config
from pylibinfra.tgbotkit.metrics import Instruments, new_instruments

if TYPE_CHECKING:  # pragma: no cover
    from aiogram import Bot
    from aiogram.methods import Response, TelegramMethod
    from aiogram.methods.base import TelegramType

# Потолок словаря per-chat локов: at-most-once чистка отпущенных локов при росте.
_LOCKS_SOFT_CAP = 10_000

GCRA_LUA = (
    "local interval = tonumber(ARGV[1])\n"
    "local now = tonumber(ARGV[2])\n"
    "local cap = tonumber(ARGV[3])\n"
    "local tat = tonumber(redis.call('GET', KEYS[1]) or now)\n"
    "if tat < now then tat = now end\n"
    "local wait = tat - now\n"
    "if wait > cap then return wait end\n"
    "redis.call('SET', KEYS[1], tat + interval, 'PX', math.ceil(tat + interval - now) + 1000)\n"
    "return wait\n"
)
"""GCRA поверх theoretical arrival time (мс) в ключе: резерв слота и ожидание до него.

Ожидание сверх `cap` (ARGV[3], мс) возвращается без резерва: иначе перелив сдвигал бы TAT
дальше в будущее на каждом вызове и лимит выключался бы до конца горизонта. TTL ключа
покрывает весь зарезервированный горизонт — короткий TTL обнулял бы лимит под burst'ом.
Redis приводит ответ Lua к целому, поэтому ожидание приходит в целых миллисекундах.
"""


class OutgoingThrottleMiddleware(BaseRequestMiddleware):
    """Держит исходящие вызовы Bot API под лимитами Telegram, не теряя сообщений.

    Telegram режет примерно 30 сообщений в секунду глобально и одно в секунду на чат:
    общий для воркеров GCRA-счётчик в Redis отдаёт время до свободного слота, и вызов
    ждёт его, а не отбрасывается — это доставка. Вызовы в один чат сериализуются
    per-chat локом, иначе burst из JetStream переставил бы сообщения местами. `429`
    (`TelegramRetryAfter`) aiogram не ретраит сам, поэтому кит спит `retry_after` и
    повторяет до `BOT_RETRY_AFTER_ATTEMPTS` раз; `retry_after` сверх `BOT_RATE_MAX_WAIT`
    не отсиживается — исключение уходит вызывающему. Недоступный Redis — fail-open: вызов
    уходит без слота, деградация видна в `tg_redis_errors_total{op=rate}`.
    """

    def __init__(
        self,
        client: redis.Redis,
        cfg: Config,
        log: Logger | None = None,
        clock: Clock | None = None,
        instruments: Instruments | None = None,
    ) -> None:
        self._log = log if log is not None else nop()
        self._clock = clock if clock is not None else Real()
        self._instr = instruments if instruments is not None else new_instruments()
        self._global_interval = _interval(cfg.rate_global)
        self._chat_interval = _interval(cfg.rate_per_chat)
        self._max_wait = cfg.rate_max_wait
        self._attempts = cfg.retry_after_attempts
        self._gcra = client.register_script(GCRA_LUA)
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock_for(self, chat_id: int) -> asyncio.Lock:
        lock = self._locks.get(chat_id)
        if lock is None:
            if len(self._locks) >= _LOCKS_SOFT_CAP:
                # Чистим только свободные локи — занятые держат незавершённый вызов.
                self._locks = {cid: lk for cid, lk in self._locks.items() if lk.locked()}
            lock = self._locks.setdefault(chat_id, asyncio.Lock())
        return lock

    async def _throttle(self, key: str, interval: float) -> None:
        if interval <= 0:
            return
        now_ms = self._clock.now().timestamp() * 1000.0
        cap_ms = self._max_wait * 1000.0
        try:
            # Скрипт уже зарезервировал слот этого вызова: ждём ОДИН раз. Повторный запрос
            # сдвинул бы TAT ещё на interval — под burst'ом это гонка до потолка ожидания.
            raw = await self._gcra(keys=[key], args=[interval, now_ms, cap_ms])
        except RedisError as exc:
            # Недоступный Redis не имеет права делать бота немым: лимит Telegram мы можем
            # превысить и получить 429, а несделанную отправку не догоняем ничем.
            self._instr.redis_errors.add(1, {"op": "rate"})
            self._log.warn("tgbotkit: rate limit unavailable, sending without a slot", err=str(exc))
            return
        wait = float(raw) / 1000.0
        if wait <= 0:
            return
        if wait > self._max_wait:
            self._log.warn("tgbotkit: rate limit wait capped, sending anyway", key=key, wait=wait)
            return
        await self._clock.sleep(wait)

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
        last = self._attempts - 1
        for attempt in range(last):
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as exc:
                if exc.retry_after > self._max_wait:
                    # Флуд-бан Telegram измеряется часами: отсидеть его здесь значит
                    # держать per-chat лок и пережить дедлайн остановки. Решение о
                    # судьбе сообщения — у вызывающего (durable-доставка повторит по NAK).
                    self._log.warn(
                        "tgbotkit: flood control retry_after over the cap, giving up",
                        retry_after=exc.retry_after,
                        cap=self._max_wait,
                    )
                    raise
                self._log.warn(
                    "tgbotkit: flood control hit, backing off",
                    retry_after=exc.retry_after,
                    attempt=attempt + 1,
                )
                await self._clock.sleep(exc.retry_after)
        return await make_request(bot, method)

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        """Выполняет вызов Bot API под лимитами; вызовы вне чата не сериализуются."""
        chat_id = getattr(method, "chat_id", None)
        if chat_id is None:
            return await self._send(make_request, bot, method)
        async with self._lock_for(chat_id):
            return await self._send(make_request, bot, method)


def _interval(rate: float) -> float:
    """Эмиссионный интервал GCRA в мс; `rate <= 0` — лимит выключен."""
    return 1000.0 / rate if rate > 0 else 0.0
