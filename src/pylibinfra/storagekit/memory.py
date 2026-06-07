"""MemoryStorage — in-memory Storage для тестов и локального dev.

Не персистентен; ключи и URL совпадают по схеме с боевым адаптером.
"""

from .errors import ErrNotFound
from .storage import Ref, new_key
from .url_builder import URLBuilder


class MemoryStorage:
    def __init__(self, urls: URLBuilder) -> None:
        self._urls = urls
        self._objs: dict[str, bytes] = {}

    async def put(self, data: bytes, content_type: str) -> Ref:
        key = new_key(content_type)
        self._objs[key] = data
        return Ref(
            key=key,
            url=self._urls.build(key),
            content_type=content_type,
            size=len(data),
        )

    async def delete(self, key: str) -> None:
        if key not in self._objs:
            raise ErrNotFound(key)
        del self._objs[key]

    def get(self, key: str) -> bytes | None:
        """Тест-хелпер: вернуть сохранённые байты (None — ключа нет)."""
        return self._objs.get(key)
