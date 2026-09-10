"""Transactor: проброс открытой транзакции через contextvar (аналог tx в Go context).

`conn()` выбирает исполнителя запроса — tx-соединение из contextvar или сам пул,
поэтому репозиторий пишется одинаково внутри транзакции и вне её.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from .errors import TransactionError
from .types import Connection, Pool, Record

Isolation = Literal["read_committed", "repeatable_read", "serializable"]


@runtime_checkable
class Executor(Protocol):
    """Структурный надтип Pool и Connection в части, которую используют conn() и DB."""

    async def execute(self, query: str, *args: Any) -> str:
        """Выполняет запрос без результата; возвращает command tag."""

    async def fetch(self, query: str, *args: Any) -> list[Record]:
        """Все строки результата."""

    async def fetchrow(self, query: str, *args: Any) -> Record | None:
        """Первая строка или None."""

    async def fetchval(self, query: str, *args: Any) -> Any:
        """Первое значение первой строки."""


@dataclass(frozen=True, slots=True)
class TxOptions:
    """Опции транзакции; None у `isolation` — уровень сервера (read committed)."""

    isolation: Isolation | None = None
    read_only: bool = False
    deferrable: bool = False


@runtime_checkable
class Transactor(Protocol):
    """Граница атомарности: `fn` выполняется в одной транзакции, проброшенной через contextvar."""

    async def with_tx[T](
        self, fn: Callable[[], Awaitable[T]], *, options: TxOptions | None = None
    ) -> T:
        """Открывает транзакцию (или участвует во внешней) и выполняет `fn` внутри неё."""


class _TestTx:
    """Маркер транзакции без соединения (`with_test_tx`): `in_tx` его видит, `conn` — нет."""


_TEST_TX = _TestTx()

# Текущее tx-соединение активного asyncio-таска; None — работаем через пул.
_current: ContextVar[Connection | _TestTx | None] = ContextVar("pgkit_current_tx", default=None)


def conn(pool: Pool) -> Executor:
    """Исполнитель запроса: tx-соединение из contextvar, иначе сам пул."""
    c = _current.get()
    if c is None or isinstance(c, _TestTx):
        return pool  # type: ignore[no-any-return]
    return c  # type: ignore[no-any-return]


def in_tx() -> bool:
    """True внутри `Transactor.with_tx` или под маркером `with_test_tx`."""
    return _current.get() is not None


def with_test_tx() -> Token[Connection | _TestTx | None]:
    """Помечает контекст «внутри транзакции» для фейков Transactor; снимает `reset_test_tx`."""
    return _current.set(_TEST_TX)


def reset_test_tx(token: Token[Connection | _TestTx | None]) -> None:
    """Снимает маркер `with_test_tx`."""
    _current.reset(token)


class PoolTransactor:
    """Transactor поверх asyncpg-пула."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def with_tx[T](
        self, fn: Callable[[], Awaitable[T]], *, options: TxOptions | None = None
    ) -> T:
        """Открывает транзакцию и выполняет `fn`; исключение и отмена откатывают её.

        Вложенный вызов участвует во внешней транзакции — её опции имеют
        приоритет, savepoint не открывается. Ошибка `fn` пробрасывается как есть;
        сорвавшийся откат добавляется к ней примечанием и причину не подменяет.
        Ошибки BEGIN/COMMIT — `pgkit.TransactionError`.
        """
        if _current.get() is not None:
            return await fn()

        opts = options or TxOptions()
        async with self._pool.acquire() as c:
            tx = c.transaction(
                isolation=opts.isolation, readonly=opts.read_only, deferrable=opts.deferrable
            )
            try:
                await tx.start()
            except Exception as exc:
                raise TransactionError(f"pgkit: begin tx: {exc}") from exc

            token = _current.set(c)
            try:
                result = await fn()
            except BaseException as exc:
                _current.reset(token)
                rollback_error = await _rollback(tx)
                if rollback_error is not None:
                    exc.add_note(f"pgkit: rollback: {rollback_error}")
                raise
            _current.reset(token)

            try:
                await tx.commit()
            except Exception as exc:
                raise TransactionError(f"pgkit: commit: {exc}") from exc
            return result


async def _rollback(tx: Any) -> Exception | None:
    """Откатывает транзакцию; ошибка отката возвращается, а не поднимается поверх причины."""
    try:
        await tx.rollback()
    except Exception as exc:
        return exc
    return None
