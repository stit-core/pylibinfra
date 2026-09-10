"""MemoryCache — in-memory `Cache` с той же семантикой TTL, резервов и тегов."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from pylibinfra.clockkit import Clock, Real

# Нижняя граница числа записей между полными проходами: у маленького кэша проход
# дёшев, дальше порог растёт вместе с числом ключей.
SWEEP_MIN = 64


@dataclass(slots=True)
class _Entry:
    value: bytes
    expires_at: datetime | None


class MemoryCache:
    """`Cache` в памяти процесса: unit-тесты консьюмера и single-replica развёртывание без Redis.

    Семантика TTL, резервов и тегов повторяет Redis, включая членство в теге,
    которое переживает удаление значения. Истёкшие ключи и мёртвое членство
    вычищаются амортизированным проходом на записи, поэтому ключ, который никто
    не читает, не живёт вечно. Данные лежат в процессе: при рестарте и на второй
    реплике их нет. Метод целиком выполняется между точками переключения задач —
    блокировка event-loop'у не нужна.
    """

    def __init__(self, clock: Clock | None = None) -> None:
        """`clock` подменяет часы: тест двигает TTL через `clockkit.Tickable` вместо сна."""
        self._clock = clock if clock is not None else Real()
        self._data: dict[str, _Entry] = {}
        self._tags: dict[str, set[str]] = {}
        self._writes_since_sweep = 0
        self._size_at_sweep = 0

    async def get(self, key: str) -> bytes | None:
        """Значение по ключу; промах и истёкший ключ — `None`."""
        return self._live(key)

    async def set(self, key: str, value: bytes, ttl: float) -> None:
        """Пишет значение с TTL в секундах (`0` — без срока жизни)."""
        self._put(key, value, ttl)

    async def set_nx(self, key: str, value: bytes, ttl: float) -> bool:
        """Пишет значение, только если живого ключа нет."""
        if self._live(key) is not None:
            return False
        self._put(key, value, ttl)
        return True

    async def get_del(self, key: str) -> bytes | None:
        """Возвращает значение и удаляет ключ."""
        value = self._live(key)
        if value is None:
            return None
        self._drop(key)
        return value

    async def compare_and_delete(self, key: str, expected: bytes) -> bool:
        """Удаляет ключ, только если его значение равно `expected`."""
        value = self._live(key)
        if value is None or value != expected:
            return False
        self._drop(key)
        return True

    async def delete(self, *keys: str) -> None:
        """Удаляет ключи; членство в тегах переживает значение, как в Redis."""
        for key in keys:
            self._drop(key)

    async def set_with_tags(self, key: str, value: bytes, ttl: float, *tags: str) -> None:
        """Пишет значение и включает ключ в наборы тегов."""
        self._put(key, value, ttl)
        for tag in tags:
            self._tags.setdefault(tag, set()).add(key)

    async def invalidate_by_tag(self, tag: str) -> None:
        """Удаляет все ключи тега и сам набор; отсутствующий тег — не ошибка."""
        for key in self._tags.pop(tag, set()):
            self._data.pop(key, None)

    async def ping(self) -> None:
        """Всегда успешен: внешней зависимости у кэша нет."""

    async def aclose(self) -> None:
        """No-op: закрывать нечего, повторный вызов тоже успешен."""

    def _live(self, key: str) -> bytes | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        if entry.expires_at is not None and self._clock.now() >= entry.expires_at:
            self._drop(key)
            return None
        return entry.value

    def _put(self, key: str, value: bytes, ttl: float) -> None:
        expires_at = self._clock.now() + timedelta(seconds=ttl) if ttl > 0 else None
        self._data[key] = _Entry(value=value, expires_at=expires_at)
        self._sweep_if_due()

    def _drop(self, key: str) -> None:
        # Членство в теге переживает значение — как `UNLINK`/`GETDEL`/истечение
        # в Redis, которые набор тега не трогают; чистит его только проход.
        self._data.pop(key, None)

    def _sweep_if_due(self) -> None:
        """Полный проход идёт, когда записей с прошлого прохода не меньше, чем было ключей.

        O(1) на запись, а мёртвых ключей и членства в тегах не накапливается
        больше, чем живых.
        """
        self._writes_since_sweep += 1
        if self._writes_since_sweep < max(self._size_at_sweep, SWEEP_MIN):
            return
        self._writes_since_sweep = 0

        now = self._clock.now()
        expired = [
            k for k, e in self._data.items() if e.expires_at is not None and now >= e.expires_at
        ]
        for key in expired:
            del self._data[key]
        for tag in list(self._tags):
            members = self._tags[tag]
            members.intersection_update(self._data)
            if not members:
                del self._tags[tag]
        self._size_at_sweep = len(self._data)
