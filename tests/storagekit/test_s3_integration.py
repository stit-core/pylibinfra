from collections.abc import AsyncIterator

import pytest

from pylibinfra.storagekit import Config, URLBuilder
from pylibinfra.storagekit.s3 import new_s3_client, new_uploader

# testcontainers.minio тянет пакет `minio`, которого нет в unit-lane (Docker-only
# зависимость) — importorskip держит non-integration gate зелёным при сборе.
pytest.importorskip("minio")
from testcontainers.minio import MinioContainer

pytestmark = pytest.mark.integration

_BUCKET = "test-bucket"
_PUBLIC_BASE = "https://cdn.example.com"
_IMAGE = "minio/minio:RELEASE.2025-01-20T14-49-07Z"


@pytest.fixture
async def uploader() -> AsyncIterator[tuple[object, Config]]:
    with MinioContainer(image=_IMAGE) as minio:
        host_ip = minio.get_container_host_ip()
        port = minio.get_exposed_port(minio.port)
        cfg = Config(
            STORAGE_S3_ENDPOINT=f"http://{host_ip}:{port}",
            STORAGE_S3_ACCESS_KEY=minio.access_key,
            STORAGE_S3_SECRET_KEY=minio.secret_key,
            STORAGE_S3_BUCKET=_BUCKET,
            STORAGE_PUBLIC_BASE_URL=_PUBLIC_BASE,
        )
        urls = URLBuilder(cfg.public_base_url, cfg.bucket)
        async with new_s3_client(cfg) as client:
            await client.create_bucket(Bucket=_BUCKET)
            yield new_uploader(client, cfg, urls), client


async def test_put_get_delete(uploader: tuple[object, object]) -> None:
    up, client = uploader  # type: ignore[misc]
    body = b"%PDF-1.7\n" * 64

    ref = await up.put(body, "application/pdf")  # type: ignore[attr-defined]
    assert ref.key
    assert ref.size == len(body)
    assert ref.content_type == "application/pdf"
    assert ref.url == f"{_PUBLIC_BASE}/{_BUCKET}/{ref.key}"

    head = await client.head_object(Bucket=_BUCKET, Key=ref.key)  # type: ignore[attr-defined]
    assert head["ContentLength"] == len(body)
    assert head["ContentType"] == "application/pdf"

    await up.delete(ref.key)  # type: ignore[attr-defined]
    with pytest.raises(Exception):  # noqa: B017, PT011
        await client.head_object(Bucket=_BUCKET, Key=ref.key)  # type: ignore[attr-defined]


async def test_put_overwrites_same_content_type(uploader: tuple[object, object]) -> None:
    up, client = uploader  # type: ignore[misc]

    ref = await up.put(b"first", "image/png")  # type: ignore[attr-defined]
    obj = await client.get_object(Bucket=_BUCKET, Key=ref.key)  # type: ignore[attr-defined]
    body = await obj["Body"].read()
    assert body == b"first"
