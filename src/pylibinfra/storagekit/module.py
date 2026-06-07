from collections.abc import AsyncIterator
from typing import Any

from dishka import Provider, Scope, provide

from .config import Config
from .s3.module import new_s3_client, new_uploader
from .s3.uploader import Uploader
from .storage import Storage
from .url_builder import URLBuilder


# Provider не provide'ит Config (asymmetry note, см. telemetry/module.py) — caller
# регистрирует Config сам. Дешёвый mode: new_config() ниже.
class StoragekitProvider(Provider):
    scope = Scope.APP

    @provide
    def url_builder(self, cfg: Config) -> URLBuilder:
        return URLBuilder(cfg.public_base_url, cfg.bucket)

    @provide
    async def s3_client(self, cfg: Config) -> AsyncIterator[Any]:
        async with new_s3_client(cfg) as client:
            yield client

    @provide(provides=Storage)
    def storage(self, client: Any, cfg: Config, urls: URLBuilder) -> Uploader:
        return new_uploader(client, cfg, urls)


def new_config() -> Config:
    return Config()
