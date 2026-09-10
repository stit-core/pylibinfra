"""start_minio — async context-manager: MinIO-контейнер, его креды и `storagekit.Config` к нему."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from testcontainers.minio import MinioContainer  # type: ignore[import-untyped]

from pylibinfra import storagekit

from ._docker import MINIO_IMAGE, quiet_testcontainers, require_docker


@dataclass(frozen=True, slots=True)
class MinioFixture:
    """Живой MinIO: `endpoint` — `host:port` без схемы (plain HTTP), креды и handle контейнера.

    Бакеты создаёт тест — фикстура их не заводит.
    """

    endpoint: str
    access_key: str
    secret_key: str
    container: MinioContainer

    def config(self, bucket: str, **overrides: Any) -> storagekit.Config:
        """`storagekit.Config` на контейнер; `overrides` перекрывают поля по имени.

        Все поля заданы явно, поэтому `STORAGE_S3_*` окружения на тест не влияют.
        Адресация — path: у MinIO из коробки нет wildcard-DNS под virtual-hosted.
        """
        values: dict[str, Any] = {
            "endpoint": self.endpoint,
            "access_key": self.access_key,
            "secret_key": self.secret_key,
            "credentials": storagekit.CREDENTIALS_STATIC,
            "bucket": bucket,
            "region": "",
            "use_ssl": False,
            "bucket_lookup": storagekit.BUCKET_LOOKUP_PATH,
            "create_bucket": False,
            "part_size": storagekit.DEFAULT_PART_SIZE,
            # Отказ в тесте должен всплыть сразу, а не после трёх повторов.
            "max_retries": 1,
            "probe_timeout": 5.0,
            "probe_on_start": False,
            "url_style": storagekit.URL_STYLE_PATH,
            "public_base_url": "",
        }
        values.update(overrides)
        return storagekit.new_config(**values)


@contextlib.asynccontextmanager
async def start_minio(*, image: str = MINIO_IMAGE) -> AsyncIterator[MinioFixture]:
    """Поднимает MinIO (`image`), отдаёт `MinioFixture`, гасит контейнер на выходе."""
    require_docker()
    quiet_testcontainers()
    container = MinioContainer(image)
    try:
        # start() внутри try: сорвавшееся ожидание готовности оставляет контейнер
        # запущенным, и снять его может только finally.
        container.start()
        cfg = container.get_config()
        yield MinioFixture(
            endpoint=str(cfg["endpoint"]),
            access_key=str(cfg["access_key"]),
            secret_key=str(cfg["secret_key"]),
            container=container,
        )
    finally:
        with contextlib.suppress(Exception):
            container.stop()
