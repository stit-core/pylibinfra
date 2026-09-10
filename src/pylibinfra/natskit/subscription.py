"""Handle подписки (`Subscription`) и учёт работающих обработчиков (`InflightGroup`)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover
    from .conn import Conn

Handler = Callable[[bytes], Awaitable[None]]
"""Обработчик входящего сообщения; subject и заголовки — через `*_from_context()`."""

Stop = Callable[[bool, float | None], Awaitable[None]]
"""Транспортная остановка подписки `(drain, budget)`; не уложилась в бюджет — `TimeoutError`."""


class Subscription(Protocol):
    """Handle одной подписки: снять интерес сразу или дренировать с ожиданием обработчиков."""

    async def unsubscribe(self) -> None:
        """Снимает интерес немедленно; работающие обработчики не ждутся.

        Полученные, но не начатые сообщения теряются — JetStream переотправит их после AckWait.
        """

    async def drain(self, *, timeout: float | None = None) -> None:  # noqa: ASYNC109
        """Снимает интерес, дообрабатывает буфер и ждёт обработчики; исчерпан — `TimeoutError`."""


class InflightGroup:
    """Счётчик работающих обработчиков с ожиданием нуля; терпит `add` во время `wait`."""

    def __init__(self) -> None:
        self._count = 0
        self._idle = asyncio.Event()
        self._idle.set()

    @property
    def count(self) -> int:
        """Сколько обработчиков работает прямо сейчас."""
        return self._count

    def add(self) -> None:
        """Регистрирует начавшийся обработчик."""
        self._count += 1
        self._idle.clear()

    def done(self) -> None:
        """Снимает завершившийся обработчик; на нуле будит ожидающих."""
        self._count -= 1
        if self._count <= 0:
            self._idle.set()

    async def wait(self, budget: float | None = None) -> None:
        """Ждёт нуля работающих обработчиков; исчерпанный `budget` — `TimeoutError`."""
        # wait_for с нулевым бюджетом отменяет ожидание, не взглянув на уже пустую группу.
        if self._idle.is_set():
            return
        await asyncio.wait_for(self._idle.wait(), budget)


class TrackedSubscription:
    """Подписка в реестре `Conn`: считает свои обработчики и умеет остановить транспорт.

    `stop(drain, budget)` — транспортная остановка: `drain=True` дообрабатывает уже
    полученные сообщения в пределах бюджета, `False` снимает интерес немедленно. Возврат
    из неё означает, что новых обработчиков не появится.
    """

    def __init__(self, conn: Conn, subject: str) -> None:
        self.subject = subject
        self.inflight = InflightGroup()
        self._conn = conn
        self._stop: Stop | None = None
        self._stopped = False
        self._tasks: set[asyncio.Task[None]] = set()

    def set_stop(self, stop: Stop) -> None:
        """Привязывает транспортную остановку — известна только после старта подписки."""
        self._stop = stop

    def spawn(self, coro: Coroutine[Any, Any, None], release: Callable[[], None]) -> None:
        """Запускает обработчик таской: считает его in-flight и освобождает слот на выходе."""
        task = asyncio.create_task(self._run(coro, release))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, coro: Coroutine[Any, Any, None], release: Callable[[], None]) -> None:
        self._conn.inflight.add()
        self.inflight.add()
        try:
            await coro
        finally:
            release()
            self.inflight.done()
            self._conn.inflight.done()

    def cancel_handlers(self) -> None:
        """Отменяет работающие обработчики — бюджет drain исчерпан."""
        for task in tuple(self._tasks):
            task.cancel()

    async def request_stop(self, *, drain: bool, budget: float | None = None) -> None:
        """Останавливает транспортную подписку ровно один раз в пределах бюджета."""
        if self._stopped or self._stop is None:
            return
        self._stopped = True
        await self._stop(drain, budget)

    async def unsubscribe(self) -> None:
        """Снимает интерес немедленно; обработчики не ждутся."""
        try:
            await self.request_stop(drain=False)
        finally:
            self._conn.forget(self)

    async def drain(self, *, timeout: float | None = None) -> None:  # noqa: ASYNC109
        """Снимает интерес, дообрабатывает буфер и ждёт обработчики в пределах `timeout`.

        Не уложившаяся в бюджет подписка остаётся в реестре `Conn`: её обработчики
        нужны `close()`, которому их ещё предстоит отменить.
        """
        await self.request_stop(drain=True, budget=timeout)
        await self.inflight.wait(timeout)
        self._conn.forget(self)
