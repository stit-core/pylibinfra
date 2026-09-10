"""MemoryStorage — in-memory `Storage` и `Presigner` для тестов и локального dev."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode

from .body import Body, chunks
from .errors import (
    DeleteFailedError,
    GetFailedError,
    NotFoundError,
    PresignFailedError,
    SizeMismatchError,
    StatFailedError,
    UploadFailedError,
)
from .storage import (
    ObjectInfo,
    ObjectReader,
    Ref,
    new_key,
    normalize_content_type,
    validate_presign_ttl,
)
from .url_builder import URLBuilder, escape_key

_READ_CHUNK = 64 * 1024

# Возвращать сохранённые байты без копии здесь безопасно: bytes неизменяемы,
# поэтому тест не может мутировать «хранилище» через выданное тело.
_Bytes = bytes


@dataclass(frozen=True, slots=True)
class _Object:
    data: bytes
    content_type: str
    etag: str
    last_modified: datetime

    def info(self) -> ObjectInfo:
        return ObjectInfo(
            size=len(self.data),
            content_type=self.content_type,
            etag=self.etag,
            last_modified=self.last_modified,
        )


class MemoryStorage:
    """In-memory реализация `Storage` и `Presigner`; метрик не эмитит.

    Контракт (size, ключи, ошибки, диапазон TTL, идемпотентный `delete`)
    совпадает с боевым адаптером — оба проходят `testkit.storagetest`. Без
    `urls` `Ref.url` — сам экранированный ключ.
    """

    def __init__(self, urls: URLBuilder | None = None) -> None:
        self._urls = urls
        self._objs: dict[str, _Object] = {}

    async def put(self, body: Body, content_type: str, size: int) -> Ref:
        """Сохраняет тело под новым ключом, соблюдая контракт `size`."""
        content_type = normalize_content_type(content_type)
        data = await _collect(body, size)
        key = new_key(content_type)
        self._objs[key] = _Object(
            data=data,
            content_type=content_type,
            etag=hashlib.sha256(data).hexdigest()[:32],
            last_modified=datetime.now(UTC),
        )
        return Ref(key=key, url=self._url(key), content_type=content_type, size=len(data))

    async def get(self, key: str) -> tuple[ObjectReader, ObjectInfo]:
        """Открывает объект на чтение; отсутствующий ключ — `NotFoundError`."""
        if not key:
            raise GetFailedError("storagekit: get: key required")
        obj = self._objs.get(key)
        if obj is None:
            raise NotFoundError(f"storagekit: get: object {key!r} not found")
        return ObjectReader.from_bytes(obj.data), obj.info()

    async def stat(self, key: str) -> ObjectInfo:
        """Метаданные объекта; отсутствующий ключ — `NotFoundError`."""
        if not key:
            raise StatFailedError("storagekit: stat: key required")
        obj = self._objs.get(key)
        if obj is None:
            raise NotFoundError(f"storagekit: stat: object {key!r} not found")
        return obj.info()

    async def delete(self, key: str) -> None:
        """Идемпотентен; пустой ключ — ошибка, как и в боевом адаптере."""
        if not key:
            raise DeleteFailedError("storagekit: delete: key required")
        self._objs.pop(key, None)

    async def presign_get(
        self,
        key: str,
        ttl: float,
        *,
        response_content_disposition: str | None = None,
        response_content_type: str | None = None,
    ) -> str:
        """Детерминированная псевдоподписанная ссылка: формат совпадает с публичным URL."""
        params = {}
        if response_content_disposition:
            params["response-content-disposition"] = response_content_disposition
        if response_content_type:
            params["response-content-type"] = response_content_type
        return self._presign("GET", key, ttl, params)

    async def presign_put(self, key: str, ttl: float) -> str:
        """Псевдоподписанная ссылка на прямую загрузку."""
        return self._presign("PUT", key, ttl, {})

    def keys(self) -> list[str]:
        """Тест-хелпер: отсортированные ключи всех сохранённых объектов."""
        return sorted(self._objs)

    def bytes(self, key: str) -> _Bytes | None:
        """Тест-хелпер: сохранённые байты по ключу; `None` — объекта нет."""
        obj = self._objs.get(key)
        return None if obj is None else obj.data

    def _presign(self, method: str, key: str, ttl: float, params: dict[str, str]) -> str:
        if not key:
            raise PresignFailedError("storagekit: presign: key required")
        validate_presign_ttl(ttl)
        params["x-presigned-method"] = method
        params["x-presigned-ttl"] = str(int(ttl))
        return f"{self._url(key)}?{urlencode(sorted(params.items()))}"

    def _url(self, key: str) -> str:
        return escape_key(key) if self._urls is None else self._urls.build(key)


async def _collect(body: Body, size: int) -> bytes:
    """Читает тело целиком, проверяя объявленный `size`; расхождение — `SizeMismatchError`."""
    buf = bytearray()
    try:
        async for chunk in chunks(body, _READ_CHUNK):
            buf += chunk
            if 0 <= size < len(buf):
                break
    except Exception as exc:
        raise UploadFailedError(f"storagekit: put: read body: {exc}") from exc
    if size >= 0 and len(buf) != size:
        raise SizeMismatchError(
            f"storagekit: put: body size differs from declared size: "
            f"declared {size}, actual {len(buf)}"
        )
    return bytes(buf)
