"""Port Storage (put/delete) + генерация ключей и Ref.

Kit доменно-нейтрален: image/file-семантика (валидация mime, размеров, декод
картинки) — на стороне приложения в доменных VO.
"""

import os
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from .errors import ErrUploadFailed


@dataclass(frozen=True, slots=True)
class Ref:
    """Стабильная ссылка на сохранённый объект. URL публичный и не протухает
    (read-side читает напрямую)."""

    key: str
    url: str
    content_type: str
    size: int


@runtime_checkable
class Storage(Protocol):
    async def put(self, data: bytes, content_type: str) -> Ref: ...

    async def delete(self, key: str) -> None: ...


def new_key(content_type: str) -> str:
    """`<uuidv7>.<ext>`; ext выводится из content_type. uuidv7 даёт
    лексикографически возрастающие ключи (k-sortable по времени)."""
    try:
        ident = _uuid7()
    except Exception as exc:
        raise ErrUploadFailed(f"uuid: {exc}") from exc
    return f"{ident}{_ext_for(content_type)}"


def _uuid7() -> UUID:
    # uuid.uuid7 нет до 3.14, новой зависимости не добавляем — собираем layout
    # сами: 48-битный unix-ms timestamp + version=7/variant=2 + случайные биты.
    ts_ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ts_ms & 0xFFFFFFFFFFFF) << 80
    value |= 0x7 << 76
    value |= ((rand >> 14) & 0x0FFF) << 64
    value |= 0b10 << 62
    value |= rand & 0x3FFFFFFFFFFFFFFF
    return UUID(int=value)


def _ext_for(content_type: str) -> str:
    # Неизвестный тип → без расширения (ключ остаётся валидным).
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "application/pdf": ".pdf",
    }.get(content_type, "")
