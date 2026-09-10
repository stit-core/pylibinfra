"""dishka-проводка боевого адаптера: клиент, порты `Storage`/`Presigner`, проба бакета."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from botocore.exceptions import ClientError
from dishka import Provider, Scope, provide

from pylibinfra import healthkit
from pylibinfra.logkit import Logger
from pylibinfra.storagekit import storage as port
from pylibinfra.storagekit.config import DEFAULT_REGION, Config
from pylibinfra.storagekit.errors import StorageError
from pylibinfra.storagekit.url_builder import URLBuilder

from .client import Client, new_client
from .storage import Storage

PROBE_NAME = "storagekit"


class S3Provider(Provider):
    """Граф адаптера: `Client` → `s3.Storage` → порты `storagekit.Storage`/`Presigner`.

    Требует в графе `Config`, `URLBuilder` (его даёт `StoragekitProvider`) и
    `logkit.Logger`; `Config` регистрирует caller (asymmetry note). Пробу
    `probe` консьюмер кладёт в свой `healthkit.Probes` сам.
    """

    scope = Scope.APP

    @provide
    async def client(self, cfg: Config, log: Logger) -> AsyncIterator[Client]:
        """Клиент S3 со стартовой проверкой бакета; закрывается на остановке контейнера."""
        async with new_client(cfg) as client:
            await ensure_bucket(client, cfg, log)
            yield client

    @provide
    def s3_storage(self, client: Client, cfg: Config, urls: URLBuilder, log: Logger) -> Storage:
        """Адаптер поверх клиента; остаётся в графе для операций вне порта."""
        return Storage(client, cfg, urls, log)

    @provide(provides=port.Storage)
    def storage(self, storage: Storage) -> Storage:
        """Порт `storagekit.Storage` — тот же адаптер."""
        return storage

    @provide(provides=port.Presigner)
    def presigner(self, storage: Storage) -> Storage:
        """Порт `storagekit.Presigner` — тот же адаптер."""
        return storage


def probe(client: Client, cfg: Config) -> healthkit.Probe:
    """Проба `storagekit`: существование бакета под дедлайном `STORAGE_S3_PROBE_TIMEOUT`."""

    async def check() -> None:
        await check_bucket(client, cfg.bucket)

    return healthkit.with_timeout(healthkit.new(PROBE_NAME, check), cfg.probe_timeout)


async def ensure_bucket(client: Client, cfg: Config, log: Logger) -> None:
    """Стартовые проверки: создаёт бакет при `create_bucket`, проверяет при `probe_on_start`.

    Отказ — `StorageError`: неверный endpoint, креды или отсутствующий бакет
    валят старт приложения, а не первый запрос.
    """
    if not cfg.create_bucket and not cfg.probe_on_start:
        return
    try:
        async with _deadline(cfg.probe_timeout):
            if cfg.create_bucket:
                await _create_bucket(client, cfg)
            if cfg.probe_on_start:
                await check_bucket(client, cfg.bucket)
                log.info("storagekit: bucket ready", bucket=cfg.bucket)
    except TimeoutError as exc:
        raise StorageError(
            f"storagekit/s3: bucket check {cfg.bucket!r} timed out after {cfg.probe_timeout:g}s"
        ) from exc


async def check_bucket(client: Client, bucket: str) -> None:
    """Проверяет доступность бакета; отсутствующий или недоступный — `StorageError`."""
    try:
        await client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        if _is_missing(exc):
            raise StorageError(f"storagekit/s3: bucket {bucket!r} not found") from exc
        raise StorageError(f"storagekit/s3: bucket exists check {bucket!r}: {exc}") from exc
    except Exception as exc:
        raise StorageError(f"storagekit/s3: bucket exists check {bucket!r}: {exc}") from exc


async def _create_bucket(client: Client, cfg: Config) -> None:
    try:
        await client.head_bucket(Bucket=cfg.bucket)
        return
    except ClientError as exc:
        if not _is_missing(exc):
            raise StorageError(f"storagekit/s3: bucket exists check {cfg.bucket!r}: {exc}") from exc
    except Exception as exc:
        raise StorageError(f"storagekit/s3: bucket exists check {cfg.bucket!r}: {exc}") from exc

    region = cfg.signing_region()
    options = (
        {}
        if region == DEFAULT_REGION
        else {"CreateBucketConfiguration": {"LocationConstraint": region}}
    )
    try:
        await client.create_bucket(Bucket=cfg.bucket, **options)
    except ClientError as exc:
        # Гонка нескольких реплик на старте: бакет уже создан соседом.
        if str(exc.response.get("Error", {}).get("Code", "")) not in (
            "BucketAlreadyOwnedByYou",
            "BucketAlreadyExists",
        ):
            raise StorageError(f"storagekit/s3: create bucket {cfg.bucket!r}: {exc}") from exc
    except Exception as exc:
        raise StorageError(f"storagekit/s3: create bucket {cfg.bucket!r}: {exc}") from exc


@asynccontextmanager
async def _deadline(seconds: float) -> AsyncIterator[None]:
    if seconds <= 0:
        yield
        return
    async with asyncio.timeout(seconds):
        yield


def _is_missing(exc: ClientError) -> bool:
    metadata = exc.response.get("ResponseMetadata", {})
    code = str(exc.response.get("Error", {}).get("Code", ""))
    return int(metadata.get("HTTPStatusCode", 0)) == 404 or code in ("404", "NoSuchBucket")
