"""Фабрики s3-клиента и Uploader'а для боевого Storage-провайдера.

Provider-сборка живёт в storagekit.module (StoragekitProvider) — здесь только
ресурс-фабрики, чтобы async-generator client lifecycle переиспользовался и в
тестах.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aioboto3

from pylibinfra.storagekit.config import Config
from pylibinfra.storagekit.url_builder import URLBuilder

from .uploader import Uploader


@asynccontextmanager
async def new_s3_client(cfg: Config) -> AsyncIterator[Any]:
    if not cfg.endpoint:
        raise ValueError("storagekit/s3: STORAGE_S3_ENDPOINT is required")
    if not cfg.bucket:
        raise ValueError("storagekit/s3: STORAGE_S3_BUCKET is required")

    scheme = "https" if cfg.use_ssl else "http"
    endpoint_url = cfg.endpoint if "://" in cfg.endpoint else f"{scheme}://{cfg.endpoint}"

    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=cfg.access_key,
        aws_secret_access_key=cfg.secret_key,
        region_name=cfg.region,
        use_ssl=cfg.use_ssl,
    ) as client:
        yield client


def new_uploader(client: Any, cfg: Config, urls: URLBuilder) -> Uploader:
    return Uploader(client, cfg.bucket, urls)
