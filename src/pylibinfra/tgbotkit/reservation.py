"""Reservation — «обработать ровно один раз» поверх общего Redis: резерв с владельцем."""

from __future__ import annotations

import math

import redis.asyncio as redis

COMMITTED = b"\x00committed"
"""Значение ключа после `commit`: свой же `release` больше не снимет зафиксированный ключ."""

CAS_COMMIT_LUA = (
    "if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end\n"
    "redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])\n"
    "return 1\n"
)
"""Поднимает ключ до длинного окна, только если резерв ещё принадлежит вызывающему."""

CAS_DELETE_LUA = (
    "if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end\n"
    "return redis.call('DEL', KEYS[1])\n"
)
"""Снимает резерв, только если он принадлежит вызывающему (compare-and-delete)."""


class Reservation:
    """Ключ «занято» с владельцем: `SET NX EX` на резерв, CAS на фиксацию и снятие.

    Резерв живёт `reserve_ttl` — столько отводится на обработку; успех поднимает ключ
    до `ttl`, транзиентный сбой снимает его сразу, чтобы переотправка не ждала окно.
    Токен владения закрывает гонку «мой резерв истёк, ключ взял соседний воркер, а я
    его снимаю»: CAS-операции чужой резерв не трогают и отвечают `False`.
    """

    def __init__(
        self,
        client: redis.Redis,
        prefix: str,
        *,
        ttl: float,
        reserve_ttl: float,
    ) -> None:
        """TTL округляются вверх до секунды: `EX` мельче не умеет, а урезать окно нельзя."""
        self._redis = client
        self._prefix = prefix
        self._ttl = max(math.ceil(ttl), 1)
        self._reserve_ttl = max(math.ceil(reserve_ttl), 1)
        self._commit = client.register_script(CAS_COMMIT_LUA)
        self._release = client.register_script(CAS_DELETE_LUA)

    async def reserve(self, key: str, token: str) -> bool:
        """`True` — ключ захвачен этим токеном; `False` — он занят или уже зафиксирован."""
        fresh = await self._redis.set(f"{self._prefix}{key}", token, nx=True, ex=self._reserve_ttl)
        return bool(fresh)

    async def commit(self, key: str, token: str) -> bool:
        """Фиксирует результат на длинное окно; `False` — резерв уже не принадлежит токену."""
        held = await self._commit(keys=[f"{self._prefix}{key}"], args=[token, COMMITTED, self._ttl])
        return bool(int(held))

    async def release(self, key: str, token: str) -> bool:
        """Снимает свой резерв; `False` — резерв уже не принадлежит токену."""
        dropped = await self._release(keys=[f"{self._prefix}{key}"], args=[token])
        return bool(int(dropped))
