"""S3-клиент aioboto3: тип для графа, фабрика и трансляция Config в botocore."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

import aioboto3
import botocore
from aiobotocore.config import AioConfig

from pylibinfra.storagekit.config import (
    BUCKET_LOOKUP_DNS,
    BUCKET_LOOKUP_PATH,
    CREDENTIALS_STATIC,
    Config,
)
from pylibinfra.storagekit.errors import ConfigError

# Транспортные дедлайны клиента: без них отказ S3 держал бы вызывающего до
# дефолтных 60 s botocore на каждой из попыток.
CONNECT_TIMEOUT = 30.0
READ_TIMEOUT = 60.0

SIGNATURE_VERSION = "s3v4"


class Client(Protocol):
    """Асинхронный S3-клиент: ключ графа и операции, которыми пользуется кит.

    Методы botocore создаёт динамически по модели сервиса, поэтому у аргументов
    здесь нет типов — типизированных стабов операций S3 в зависимостях нет.
    """

    async def put_object(self, **kwargs: Any) -> Any:
        """PutObject: тело одним запросом."""
        ...

    async def get_object(self, **kwargs: Any) -> Any:
        """GetObject: метаданные и поток тела."""
        ...

    async def head_object(self, **kwargs: Any) -> Any:
        """HeadObject: метаданные объекта."""
        ...

    async def delete_object(self, **kwargs: Any) -> Any:
        """DeleteObject: удаление объекта (идемпотентно)."""
        ...

    async def head_bucket(self, **kwargs: Any) -> Any:
        """HeadBucket: существование и доступность бакета."""
        ...

    async def create_bucket(self, **kwargs: Any) -> Any:
        """CreateBucket: создание бакета при `STORAGE_S3_CREATE_BUCKET`."""
        ...

    async def list_objects_v2(self, **kwargs: Any) -> Any:
        """ListObjectsV2: перечисление объектов бакета (операция вне порта)."""
        ...

    async def create_multipart_upload(self, **kwargs: Any) -> Any:
        """CreateMultipartUpload: начало многочастной загрузки."""
        ...

    async def upload_part(self, **kwargs: Any) -> Any:
        """UploadPart: одна часть многочастной загрузки."""
        ...

    async def complete_multipart_upload(self, **kwargs: Any) -> Any:
        """CompleteMultipartUpload: сборка объекта из загруженных частей."""
        ...

    async def abort_multipart_upload(self, **kwargs: Any) -> Any:
        """AbortMultipartUpload: снятие начатой загрузки вместе с её частями."""
        ...

    async def generate_presigned_url(self, *args: Any, **kwargs: Any) -> Any:
        """Подпись presigned-ссылки; операция локальная, сети не требует."""
        ...


@asynccontextmanager
async def new_client(cfg: Config) -> AsyncIterator[Client]:
    """Открывает S3-клиент по `Config` и закрывает его на выходе; отказ сборки — `ConfigError`."""
    access, secret = _static_credentials(cfg)
    try:
        session = aioboto3.Session()
        client = session.client(
            "s3",
            endpoint_url=cfg.endpoint_url(),
            region_name=cfg.signing_region(),
            aws_access_key_id=access,
            aws_secret_access_key=secret,
            config=_boto_config(
                cfg, anonymous=cfg.credentials == CREDENTIALS_STATIC and not access
            ),
        )
    except Exception as exc:
        raise ConfigError(f"storagekit/s3: new client: {exc}") from exc
    async with client as opened:
        yield opened


def _static_credentials(cfg: Config) -> tuple[str | None, str | None]:
    """Ключи режима `static`; в режиме `chain` их ищет цепочка провайдеров botocore."""
    if cfg.credentials != CREDENTIALS_STATIC:
        return None, None
    access = cfg.access_key.get_secret_value()
    if not access:
        return None, None
    return access, cfg.secret_key.get_secret_value()


def _boto_config(cfg: Config, *, anonymous: bool) -> AioConfig:
    options: dict[str, Any] = {
        # botocore считает попытки, конфиг — повторы после первой.
        "retries": {"max_attempts": cfg.max_retries + 1, "mode": "standard"},
        "s3": {"addressing_style": _addressing_style(cfg.bucket_lookup)},
        "connect_timeout": CONNECT_TIMEOUT,
        "read_timeout": READ_TIMEOUT,
        # У произвольного endpoint'а botocore подписывает SigV2, которую
        # S3-совместимые хранилища уже не принимают.
        "signature_version": SIGNATURE_VERSION,
    }
    if anonymous:
        # Оба ключа пусты — анонимный доступ: без UNSIGNED botocore пошёл бы
        # искать креды в окружении и подписал бы запрос чужими.
        options["signature_version"] = botocore.UNSIGNED
    return AioConfig(**options)


def _addressing_style(bucket_lookup: str) -> str:
    if bucket_lookup == BUCKET_LOOKUP_PATH:
        return "path"
    if bucket_lookup == BUCKET_LOOKUP_DNS:
        return "virtual"
    return "auto"
