"""Детерминированные фейки Clock для тестов."""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta


class Fixed:
    """Часы, застывшие на `t`; `sleep(seconds > 0)` не завершается никогда."""

    def __init__(self, t: datetime) -> None:
        self._t = t

    def now(self) -> datetime:
        """Застывший момент."""
        return self._t

    def since(self, t: datetime) -> float:
        """Секунды между застывшим моментом и `t` (отрицательные, если `t` в будущем)."""
        return (self._t - t).total_seconds()

    async def sleep(self, seconds: float) -> None:
        """Мгновенно при `seconds <= 0`, иначе ждёт вечно — время не движется."""
        if seconds <= 0:
            return
        await asyncio.get_running_loop().create_future()


class Tickable:
    """Часы, которые двигает тест: `advance`/`set` будят `sleep` с наступившим дедлайном.

    Очередь спящих под мьютексом, а пробуждение идёт через цикл самого спящего,
    поэтому двигать часы можно и из другого потока (`asyncio.to_thread`, воркер
    кита): в своём цикле `sleep` просыпается внутри вызова `advance`/`set`, в
    чужом — ближайшим шагом того цикла.
    """

    def __init__(self, start: datetime) -> None:
        self._t = start
        self._lock = threading.Lock()
        self._sleepers: list[tuple[datetime, asyncio.Future[None]]] = []
        self._added = asyncio.Event()

    def now(self) -> datetime:
        """Текущий момент часов."""
        with self._lock:
            return self._t

    def since(self, t: datetime) -> float:
        """Секунды между текущим моментом часов и `t`."""
        return (self.now() - t).total_seconds()

    async def sleep(self, seconds: float) -> None:
        """Засыпает до момента `now + seconds`; просыпается внутри `advance`/`set`."""
        if seconds <= 0:
            return
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        with self._lock:
            self._sleepers.append((self._t + timedelta(seconds=seconds), fut))
        self._added.set()
        try:
            await fut
        finally:
            with self._lock:
                self._sleepers = [(dl, f) for dl, f in self._sleepers if f is not fut]

    def advance(self, delta: timedelta | float) -> None:
        """Сдвигает часы вперёд (timedelta или секунды) и будит sleep'ы с наступившим дедлайном."""
        d = delta if isinstance(delta, timedelta) else timedelta(seconds=delta)
        with self._lock:
            self._t += d
            due = self._take_due_locked()
        _wake_all(due)

    def set(self, t: datetime) -> None:
        """Ставит часы в `t` (в том числе в прошлое) и будит sleep'ы с наступившим дедлайном."""
        with self._lock:
            self._t = t
            due = self._take_due_locked()
        _wake_all(due)

    def sleepers(self) -> int:
        """Число sleep'ов, ждущих продвижения часов."""
        with self._lock:
            return len(self._sleepers)

    async def wait_sleepers(self, n: int) -> None:
        """Ждёт, пока не наберётся `n` ожидающих sleep'ов — снимает гонку «таска ещё не заснула»."""
        while True:
            # Сброс до проверки: сон, добавленный после неё, взводит событие заново.
            self._added.clear()
            if self.sleepers() >= n:
                return
            await self._added.wait()

    def _take_due_locked(self) -> list[asyncio.Future[None]]:
        due = [f for dl, f in self._sleepers if dl <= self._t]
        self._sleepers = [(dl, f) for dl, f in self._sleepers if dl > self._t]
        return due


def _wake_all(futures: list[asyncio.Future[None]]) -> None:
    for fut in futures:
        _wake(fut)


def _wake(fut: asyncio.Future[None]) -> None:
    loop = fut.get_loop()
    try:
        running: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        _resolve(fut)
        return
    try:
        loop.call_soon_threadsafe(_resolve, fut)
    except RuntimeError:
        # Цикл спящего уже закрыт (тест закончился) — будить некого.
        return


def _resolve(fut: asyncio.Future[None]) -> None:
    if not fut.done():
        fut.set_result(None)
