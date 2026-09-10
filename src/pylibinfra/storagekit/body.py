"""Тело `put`: допустимые источники и нарезка на части фиксированного размера."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterable, AsyncIterator, Awaitable
from typing import Protocol, runtime_checkable


@runtime_checkable
class Readable(Protocol):
    """File-like источник: `read(n)` синхронный или корутина (файл, `BytesIO`, поток S3)."""

    def read(self, n: int = -1, /) -> bytes | Awaitable[bytes]:
        """Возвращает до n байт; пустые байты — конец тела."""
        ...


Body = bytes | bytearray | memoryview | AsyncIterable[bytes] | Readable
"""Источник тела: готовые байты, async-итератор чанков или file-like с `read`."""


async def chunks(body: Body, size: int) -> AsyncIterator[bytes]:
    """Отдаёт тело чанками до `size` байт; форма чанков источника не сохраняется."""
    if isinstance(body, bytes | bytearray | memoryview):
        data = bytes(body)
        for start in range(0, len(data), size):
            yield data[start : start + size]
        return
    if isinstance(body, Readable):
        while True:
            chunk = body.read(size)
            if inspect.isawaitable(chunk):
                chunk = await chunk
            if not chunk:
                return
            yield bytes(chunk)
    async for chunk in body:
        if chunk:
            yield bytes(chunk)


class PartStream:
    """Режет поток чанков на части ровно по `part_size`; последняя часть — остаток."""

    def __init__(self, body: Body, part_size: int) -> None:
        self._chunks = chunks(body, part_size)
        self._part_size = part_size
        self._buf = bytearray()
        self._eof = False

    async def next_part(self) -> bytes:
        """Следующая часть; пустые байты — тело кончилось."""
        while not self._eof and len(self._buf) < self._part_size:
            chunk = await anext(self._chunks, b"")
            if not chunk:
                self._eof = True
                break
            self._buf += chunk
        part = bytes(self._buf[: self._part_size])
        del self._buf[: self._part_size]
        return part

    async def more(self) -> bool:
        """True, если в теле остался хотя бы байт; читает не больше одного чанка сверх буфера."""
        while not self._buf and not self._eof:
            chunk = await anext(self._chunks, b"")
            if not chunk:
                self._eof = True
                return False
            self._buf += chunk
        return bool(self._buf)
