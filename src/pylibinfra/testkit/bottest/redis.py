"""MemoryRedis — in-memory дубль `redis.Redis` для unit-тестов бота без Docker."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Any

from redis.exceptions import RedisError

from pylibinfra.clockkit import Clock, Real
from pylibinfra.tgbotkit.middleware import GCRA_LUA, INCR_WINDOW_LUA
from pylibinfra.tgbotkit.reservation import CAS_COMMIT_LUA, CAS_DELETE_LUA


@dataclass(slots=True)
class _Entry:
    """Значение и момент истечения (по часам фейка); `0.0` — ключ без срока."""

    value: Any
    expires_at: float = 0.0


class _Script:
    """Общая часть дублей Lua-скриптов: работают командами того же клиента, что и кит."""

    def __init__(self, owner: MemoryRedis) -> None:
        self._owner = owner


class _IncrWindow(_Script):
    """Дубль `INCR_WINDOW_LUA`: инкремент счётчика окна и TTL на первом инкременте."""

    async def __call__(
        self, keys: Sequence[str] | None = None, args: Sequence[Any] | None = None
    ) -> int:
        key = (keys or [""])[0]
        value = int(await self._owner.incr(key))
        if value == 1:
            await self._owner.expire(key, int(float((args or [1])[0])))
        return value


class _Gcra(_Script):
    """Дубль `GCRA_LUA`: TAT в мс, резерв слота и целочисленное ожидание, как у Redis."""

    async def __call__(
        self, keys: Sequence[str] | None = None, args: Sequence[Any] | None = None
    ) -> int:
        key = (keys or [""])[0]
        interval, now, cap = (float(x) for x in (args or [0.0, 0.0, 0.0])[:3])
        stored = await self._owner.get(key)
        tat = max(float(stored) if stored is not None else now, now)
        wait = tat - now
        if wait > cap:
            return int(wait)
        await self._owner.set(key, tat + interval, px=math.ceil(tat + interval - now) + 1000)
        # Redis приводит ответ Lua к целому — фейк обязан врать так же, иначе тест
        # увидит дробное ожидание, которого в проде не бывает.
        return int(wait)


class _CasCommit(_Script):
    """Дубль `CAS_COMMIT_LUA`: продлевает ключ до длинного окна, если токен совпал."""

    async def __call__(
        self, keys: Sequence[str] | None = None, args: Sequence[Any] | None = None
    ) -> int:
        key = (keys or [""])[0]
        token, committed, ttl = (args or ["", b"", 1])[:3]
        if not await self._owner.holds(key, token):
            return 0
        await self._owner.set(key, committed, ex=int(ttl))
        return 1


class _CasDelete(_Script):
    """Дубль `CAS_DELETE_LUA`: снимает ключ, только если токен совпал."""

    async def __call__(
        self, keys: Sequence[str] | None = None, args: Sequence[Any] | None = None
    ) -> int:
        key = (keys or [""])[0]
        if not await self._owner.holds(key, (args or [""])[0]):
            return 0
        return int(await self._owner.delete(key))


class MemoryLock:
    """Дубль распределённого лока `redis.lock`: asyncio-лок с тем же async-CM-протоколом."""

    def __init__(self, owner: MemoryRedis, name: str) -> None:
        self._owner = owner
        self._name = name

    async def __aenter__(self) -> MemoryLock:
        """Захватывает лок; при `fail=True` — `RedisError`, как недоступный сервер."""
        self._owner.check()
        await self._owner.locks.setdefault(self._name, asyncio.Lock()).acquire()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Освобождает лок."""
        self._owner.locks[self._name].release()


class MemoryRedis:
    """Подмножество `redis.Redis`, которым пользуется tgbotkit: `SET NX EX`, `DELETE`, скрипты.

    Знает ровно Lua-скрипты кита (`INCR_WINDOW_LUA`, `GCRA_LUA`, CAS-пара резервов) —
    чужой скрипт отвергается, чтобы фейк не расходился с боевым Redis молча. TTL
    моделируется по `clockkit.Clock`: с `Tickable` тест двигает время и проверяет
    истечение маркеров дедупа и окон троттлинга без сна. `fail=True` моделирует
    недоступное хранилище: команды бросают `RedisError` и проверяют fail-open-пути.
    """

    def __init__(self, fail: bool = False, clock: Clock | None = None) -> None:
        """`clock` двигает TTL: дефолт — реальные часы, `Tickable` — часы теста."""
        self.locks: dict[str, asyncio.Lock] = {}
        self.fail = fail
        self._clock = clock if clock is not None else Real()
        self._data: dict[str, _Entry] = {}

    @property
    def store(self) -> dict[str, Any]:
        """Снимок живых ключей — для assert'ов теста, не для мутаций."""
        return {key: entry.value for key, entry in self._data.items() if not self._expired(entry)}

    def lock(self, name: str, **kwargs: Any) -> MemoryLock:
        """Лок по имени — то, чем `RedisEventIsolation` разводит апдейты одного чата."""
        return MemoryLock(self, name)

    async def set(
        self, key: str, value: Any, nx: bool = False, ex: Any = None, px: Any = None
    ) -> bool | None:
        """`SET` с опциями `NX`/`EX`/`PX`; `None` — ключ занят и при `nx=True` не переписан."""
        self.check()
        if nx and self._live(key) is not None:
            return None
        ttl = float(ex) if ex is not None else (float(px) / 1000.0 if px is not None else 0.0)
        self._data[key] = _Entry(value, self._deadline(ttl))
        return True

    async def get(self, key: str) -> Any:
        """Значение ключа или `None`, если его нет или он истёк."""
        self.check()
        return self._live(key)

    async def delete(self, *keys: str) -> int:
        """Удаляет ключи и отдаёт число удалённых (истёкшие не считаются)."""
        self.check()
        return sum(self._drop(key) for key in keys)

    async def incr(self, key: str) -> int:
        """Инкремент счётчика; отсутствующий или истёкший ключ считается нулём."""
        self.check()
        value = int(self._live(key) or 0) + 1
        # INCR существующему ключу TTL не трогает, новому — заводит без срока.
        entry = self._data.get(key)
        expires_at = entry.expires_at if entry is not None and not self._expired(entry) else 0.0
        self._data[key] = _Entry(value, expires_at)
        return value

    async def expire(self, key: str, ttl: int) -> bool:
        """Ставит TTL живому ключу; `False` — ключа нет."""
        self.check()
        if self._live(key) is None:
            return False
        self._data[key].expires_at = self._deadline(float(ttl))
        return True

    async def ttl(self, key: str) -> int:
        """Остаток TTL в секундах: `-2` — ключа нет, `-1` — он без срока.

        Округление к ближайшей секунде (половина вверх) — как у боевого `TTL`: ключ,
        поставленный через `PX 1100`, там отвечает `1`, а не `2`, и тест на TTL
        маркера иначе расходился бы с интеграционным.
        """
        self.check()
        if self._live(key) is None:
            return -2
        entry = self._data[key]
        if not entry.expires_at:
            return -1
        return math.floor(entry.expires_at - self._clock.now().timestamp() + 0.5)

    async def holds(self, key: str, token: Any) -> bool:
        """`True`, если живой ключ хранит ровно этот токен (сверка как в CAS-скриптах)."""
        self.check()
        value = self._live(key)
        if value is None:
            return False
        return _as_bytes(value) == _as_bytes(token)

    async def ping(self) -> bool:
        """`PING`; при `fail=True` — `RedisError`, как недоступный сервер."""
        self.check()
        return True

    async def aclose(self) -> None:
        """Закрытие клиента: у фейка no-op."""

    def register_script(self, script: str) -> _IncrWindow | _Gcra | _CasCommit | _CasDelete:
        """Дубль зарегистрированного Lua-скрипта; неизвестный скрипт — `RedisError`."""
        if script == INCR_WINDOW_LUA:
            return _IncrWindow(self)
        if script == GCRA_LUA:
            return _Gcra(self)
        if script == CAS_COMMIT_LUA:
            return _CasCommit(self)
        if script == CAS_DELETE_LUA:
            return _CasDelete(self)
        raise RedisError("bottest: MemoryRedis knows only tgbotkit scripts")

    def check(self) -> None:
        """Бросает `RedisError` при `fail=True`; зовётся каждой командой и каждым скриптом."""
        if self.fail:
            raise RedisError("bottest: redis is down")

    def _deadline(self, ttl: float) -> float:
        return self._clock.now().timestamp() + ttl if ttl > 0 else 0.0

    def _expired(self, entry: _Entry) -> bool:
        return bool(entry.expires_at) and entry.expires_at <= self._clock.now().timestamp()

    def _live(self, key: str) -> Any:
        entry = self._data.get(key)
        if entry is None:
            return None
        if self._expired(entry):
            del self._data[key]
            return None
        return entry.value

    def _drop(self, key: str) -> bool:
        existed = self._live(key) is not None
        self._data.pop(key, None)
        return existed


def _as_bytes(value: Any) -> bytes:
    return value if isinstance(value, bytes) else str(value).encode()
