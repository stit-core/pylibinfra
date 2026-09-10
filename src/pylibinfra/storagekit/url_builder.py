"""URLBuilder — публичный URL объекта и экранирование сегментов ключа."""

from __future__ import annotations

from urllib.parse import quote

from .config import URL_STYLE_PATH, URL_STYLE_VIRTUAL


class URLBuilder:
    """Строит публичный URL: `<base>/<bucket>/<key>` (path) или `<base>/<key>` (virtual).

    `virtual` — бакет уже в хосте base URL либо за CDN.
    """

    def __init__(self, base_url: str, bucket: str, style: str = URL_STYLE_PATH) -> None:
        self._base_url = base_url.rstrip("/")
        self._bucket = bucket
        self._style = style or URL_STYLE_PATH

    def build(self, key: str) -> str:
        """URL ключа; `key` — object key, а не путь: сегменты экранируются, `/` сохраняются."""
        prefix = self._base_url
        if self._style != URL_STYLE_VIRTUAL and self._bucket:
            prefix += "/" + quote(self._bucket, safe="")
        return f"{prefix}/{escape_key(key)}"


def escape_key(key: str) -> str:
    """Экранирует каждый сегмент ключа, сохраняя разделители `/`."""
    return "/".join(quote(segment, safe="") for segment in key.split("/"))
