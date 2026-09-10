"""Порт `Storage`/`Presigner`, `Ref`/`ObjectInfo`/`ObjectReader` и генерация ключей."""

from __future__ import annotations

import mimetypes
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self, runtime_checkable

from pylibinfra.idkit import new_v7

from .body import Body
from .errors import PresignFailedError

# Пустой content-type S3 всё равно сохранит этим типом — Ref не должен
# расходиться с метаданными объекта.
DEFAULT_CONTENT_TYPE = "application/octet-stream"

MIN_PRESIGN_TTL = 1.0
MAX_PRESIGN_TTL = 7 * 24 * 60 * 60.0

# Расширения частых типов: mime-база ОС отличается от машины к машине, а ключ
# объекта должен воспроизводиться одинаково везде.
_CANONICAL_EXT = {
    "application/json": ".json",
    "application/octet-stream": "",
    "application/pdf": ".pdf",
    "application/xml": ".xml",
    "application/zip": ".zip",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/webm": ".weba",
    "image/avif": ".avif",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/svg+xml": ".svg",
    "image/webp": ".webp",
    "text/csv": ".csv",
    "text/html": ".html",
    "text/plain": ".txt",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}


@dataclass(frozen=True, slots=True)
class Ref:
    """Стабильная ссылка на сохранённый объект; `url` публичный и не протухает."""

    key: str
    url: str
    content_type: str
    size: int


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    """Метаданные объекта в хранилище."""

    size: int
    content_type: str
    etag: str
    last_modified: datetime


class ObjectReader:
    """Тело объекта: `async for` по чанкам, `read()` целиком или по n байт, `aclose()`.

    Пригоден как async-context-manager. Закрывает вызывающий — иначе соединение
    до хранилища не вернётся в пул.
    """

    def __init__(
        self,
        chunks: AsyncIterator[bytes],
        *,
        close: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._chunks = chunks
        self._close = close
        self._buf = bytearray()
        self._eof = False

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        """Ридер поверх готовых байтов — для in-memory реализаций и тестов."""

        async def once() -> AsyncIterator[bytes]:
            if data:
                yield data

        return cls(once())

    async def read(self, n: int = -1) -> bytes:
        """Читает n байт (при n < 0 — всё тело); пустые байты — тело кончилось."""
        while not self._eof and (n < 0 or len(self._buf) < n):
            chunk = await anext(self._chunks, b"")
            if not chunk:
                self._eof = True
                break
            self._buf += chunk
        if n < 0:
            out = bytes(self._buf)
            self._buf.clear()
            return out
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def __aiter__(self) -> AsyncIterator[bytes]:
        """Итерирует тело чанками так, как их отдаёт хранилище."""
        return self._iter()

    async def aclose(self) -> None:
        """Закрывает поток и источник чанков; повторный вызов безопасен."""
        self._eof = True
        self._buf.clear()
        # Недочитанный генератор чанков иначе доживает до сборщика мусора и
        # финализируется вне «своего» loop'а.
        if isinstance(self._chunks, AsyncGenerator):
            await self._chunks.aclose()
        close, self._close = self._close, None
        if close is not None:
            await close()

    async def __aenter__(self) -> Self:
        """Отдаёт сам ридер — тело читают внутри `async with`."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Закрывает поток на выходе из `async with`."""
        await self.aclose()

    async def _iter(self) -> AsyncIterator[bytes]:
        if self._buf:
            head = bytes(self._buf)
            self._buf.clear()
            yield head
        while not self._eof:
            chunk = await anext(self._chunks, b"")
            if not chunk:
                self._eof = True
                return
            yield chunk


@runtime_checkable
class Storage(Protocol):
    """Порт объектного хранилища."""

    async def put(self, body: Body, content_type: str, size: int) -> Ref:
        """Сохраняет тело под сгенерированным ключом и отдаёт `Ref`.

        `size >= 0` — точная длина: тело короче или длиннее даёт
        `SizeMismatchError`, объект не остаётся. `size < 0` — длина неизвестна,
        тело стримится до конца, `Ref.size` — фактический размер. Пустой
        `content_type` нормализуется в `DEFAULT_CONTENT_TYPE`.
        """
        ...

    async def get(self, key: str) -> tuple[ObjectReader, ObjectInfo]:
        """Открывает объект на чтение; ридер закрывает вызывающий, нет объекта — `NotFoundError`."""
        ...

    async def stat(self, key: str) -> ObjectInfo:
        """Метаданные объекта; нет объекта — `NotFoundError`."""
        ...

    async def delete(self, key: str) -> None:
        """Идемпотентен: отсутствующий ключ — не ошибка. Пустой ключ — `DeleteFailedError`."""
        ...


@runtime_checkable
class Presigner(Protocol):
    """Краткоживущие presigned-ссылки на объекты сконфигуренного бакета.

    Читатель (или загрузчик) обращается к хранилищу напрямую, без собственных
    кредов; `ttl` вне `[MIN_PRESIGN_TTL, MAX_PRESIGN_TTL]` — `PresignFailedError`.
    """

    async def presign_get(
        self,
        key: str,
        ttl: float,
        *,
        response_content_disposition: str | None = None,
        response_content_type: str | None = None,
    ) -> str:
        """Подписывает GET-ссылку; `response_*` кладут в неё headers ответа хранилища."""
        ...

    async def presign_put(self, key: str, ttl: float) -> str:
        """Подписывает PUT-ссылку для прямой загрузки клиентом."""
        ...


def validate_presign_ttl(ttl: float) -> None:
    """Проверяет срок жизни ссылки; вне диапазона — `PresignFailedError`."""
    if not MIN_PRESIGN_TTL <= ttl <= MAX_PRESIGN_TTL:
        raise PresignFailedError(
            f"storagekit: ttl {ttl:g}s outside [{MIN_PRESIGN_TTL:g}s, {MAX_PRESIGN_TTL:g}s]"
        )


def normalize_content_type(content_type: str) -> str:
    """Заменяет пустой content-type на `DEFAULT_CONTENT_TYPE`."""
    return content_type if content_type.strip() else DEFAULT_CONTENT_TYPE


def new_key(content_type: str) -> str:
    """`<uuidv7>.<ext>`; uuidv7 даёт лексикографически возрастающие ключи (k-sortable)."""
    return f"{new_v7()}{_ext_for(content_type)}"


def _ext_for(content_type: str) -> str:
    media_type = content_type.partition(";")[0].strip().lower()
    if media_type in _CANONICAL_EXT:
        return _CANONICAL_EXT[media_type]
    return mimetypes.guess_extension(media_type) or ""
