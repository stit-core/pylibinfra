"""dishka-проводка ядра кита: `URLBuilder` из `Config`."""

from __future__ import annotations

from dishka import Provider, Scope, provide

from .config import Config
from .url_builder import URLBuilder


class StoragekitProvider(Provider):
    """Граф ядра: `Config` → `URLBuilder`.

    `Config` регистрирует caller (asymmetry note); боевые `Storage`/`Presigner`
    даёт `storagekit.s3.S3Provider`.
    """

    scope = Scope.APP

    @provide
    def url_builder(self, cfg: Config) -> URLBuilder:
        """Построитель публичных URL по `STORAGE_PUBLIC_BASE_URL`/`_BUCKET`/`_URL_STYLE`."""
        return URLBuilder(cfg.public_base_url, cfg.bucket, cfg.url_style)
