"""Фейковые Transactor'ы и прогон в откатываемой транзакции на реальном пуле."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable

from pylibinfra.pgkit.transactor import TxOptions, _current, reset_test_tx, with_test_tx
from pylibinfra.pgkit.types import Connection, Pool


class Identity:
    """Transactor без транзакции: вызывает fn под маркером `with_test_tx` (`in_tx` — True)."""

    async def with_tx[T](
        self, fn: Callable[[], Awaitable[T]], *, options: TxOptions | None = None
    ) -> T:
        """Вызывает fn без транзакции, но под маркером `with_test_tx`; опции игнорируются."""
        token = with_test_tx()
        try:
            return await fn()
        finally:
            reset_test_tx(token)


class Recorder:
    """Identity со счётчиком вызовов: тест проверяет, что запись шла через транзакцию."""

    def __init__(self) -> None:
        self._calls = 0

    async def with_tx[T](
        self, fn: Callable[[], Awaitable[T]], *, options: TxOptions | None = None
    ) -> T:
        """Считает вызов и выполняет fn под маркером `with_test_tx`."""
        self._calls += 1
        token = with_test_tx()
        try:
            return await fn()
        finally:
            reset_test_tx(token)

    def calls(self) -> int:
        """Сколько раз звали `with_tx`."""
        return self._calls


@contextlib.asynccontextmanager
async def in_rollback_tx(pool: Pool) -> AsyncIterator[Connection]:
    """Открывает транзакцию на пуле и откатывает её на выходе — БД остаётся чистой.

    Соединение кладётся в contextvar pgkit, поэтому `DB` и `Transactor` внутри
    блока видят именно его.
    """
    async with pool.acquire() as c:
        conn: Connection = c
        tx = conn.transaction()
        await tx.start()
        token = _current.set(conn)
        try:
            yield conn
        finally:
            _current.reset(token)
            await tx.rollback()
