"""s3.Storage на живом MinIO: контракт, multipart, стартовые проверки, presign round-trip."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack
from itertools import count
from typing import Any

import httpx
import pytest
import pytest_asyncio
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.logkit import Logger, nop
from pylibinfra.storagekit import (
    MIN_PART_SIZE,
    MIN_PRESIGN_TTL,
    Config,
    NotFoundError,
    Presigner,
    SizeMismatchError,
    Storage,
    StorageError,
    StoragekitProvider,
    URLBuilder,
)
from pylibinfra.storagekit import s3 as s3kit
from pylibinfra.testkit import MinioFixture, start_minio, storagetest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_BUCKETS = count(1)

StorageFactory = Callable[..., Awaitable[tuple[s3kit.Storage, s3kit.Client, Config]]]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def minio() -> AsyncIterator[MinioFixture]:
    async with start_minio() as fixture:
        yield fixture


@pytest_asyncio.fixture(loop_scope="module")
async def clients(minio: MinioFixture) -> AsyncIterator[Callable[..., Awaitable[Any]]]:
    """Фабрика клиентов на общий контейнер; каждый закрывается на выходе из теста."""
    async with AsyncExitStack() as stack:

        async def make(**overrides: Any) -> tuple[s3kit.Client, Config]:
            cfg = minio.config(f"test-bucket-{next(_BUCKETS)}", **overrides)
            client = await stack.enter_async_context(s3kit.new_client(cfg))
            return client, cfg

        yield make


@pytest_asyncio.fixture(loop_scope="module")
async def storages(clients: Callable[..., Awaitable[Any]]) -> StorageFactory:
    """Фабрика адаптеров: свой бакет на вызов, бакет уже создан."""

    async def make(**overrides: Any) -> tuple[s3kit.Storage, s3kit.Client, Config]:
        client, cfg = await clients(**overrides)
        await client.create_bucket(Bucket=cfg.bucket)
        urls = URLBuilder("https://cdn.example.com", cfg.bucket, cfg.url_style)
        return s3kit.Storage(client, cfg, urls, nop()), client, cfg

    return make


async def list_keys(client: s3kit.Client, bucket: str) -> list[str]:
    response = await client.list_objects_v2(Bucket=bucket)
    return [str(item["Key"]) for item in response.get("Contents", [])]


@pytest.mark.parametrize("case", list(storagetest.CASES))
async def test_contract(case: str, storages: StorageFactory) -> None:
    storage, client, cfg = await storages()

    async def keys() -> list[str]:
        return await list_keys(client, cfg.bucket)

    await storagetest.run_case(case, storagetest.Subject(storage, storage, keys=keys))


async def test_adapter_satisfies_ports(storages: StorageFactory) -> None:
    storage, _, _ = await storages()

    assert isinstance(storage, Storage)
    assert isinstance(storage, Presigner)


async def test_multipart_upload_with_declared_size(storages: StorageFactory) -> None:
    storage, _, _ = await storages(part_size=MIN_PART_SIZE)
    body = b"multipart-" * ((2 * MIN_PART_SIZE) // 10 + 1)

    ref = await storage.put(body, "application/octet-stream", len(body))

    info = await storage.stat(ref.key)
    assert ref.size == len(body)
    assert info.size == len(body)
    # ETag multipart-объекта — «<md5>-<число частей>».
    assert "-" in info.etag


async def test_multipart_upload_with_unknown_size(storages: StorageFactory) -> None:
    storage, _, _ = await storages(part_size=MIN_PART_SIZE)
    body = b"s" * (2 * MIN_PART_SIZE + 1024)

    async def stream() -> AsyncIterator[bytes]:
        for start in range(0, len(body), 64 * 1024):
            yield body[start : start + 64 * 1024]

    ref = await storage.put(stream(), "application/octet-stream", -1)

    assert ref.size == len(body)
    reader, info = await storage.get(ref.key)
    async with reader:
        stored = await reader.read()
    assert stored == body
    assert info.size == len(body)


async def test_single_put_boundary_is_inclusive(storages: StorageFactory) -> None:
    storage, _, _ = await storages(part_size=MIN_PART_SIZE)

    exact = await storage.put(b"b" * MIN_PART_SIZE, "application/octet-stream", MIN_PART_SIZE)
    over = await storage.put(
        b"b" * (MIN_PART_SIZE + 1), "application/octet-stream", MIN_PART_SIZE + 1
    )

    assert "-" not in (await storage.stat(exact.key)).etag
    assert "-" in (await storage.stat(over.key)).etag


async def test_size_mismatch_leaves_no_object(storages: StorageFactory) -> None:
    storage, client, cfg = await storages(part_size=MIN_PART_SIZE)
    body = b"0123456789"

    async def stream() -> AsyncIterator[bytes]:
        yield body

    for source in (body, stream()):
        for declared in (4, 40):
            with pytest.raises(SizeMismatchError):
                await storage.put(source, "application/pdf", declared)

    big = b"m" * (MIN_PART_SIZE + 32)
    with pytest.raises(SizeMismatchError):
        await storage.put(big, "application/octet-stream", MIN_PART_SIZE + 64)

    assert await list_keys(client, cfg.bucket) == []


async def test_delete_is_idempotent(storages: StorageFactory) -> None:
    storage, _, _ = await storages()
    ref = await storage.put(b"payload", "text/plain", 7)

    await storage.delete(ref.key)
    await storage.delete(ref.key)

    with pytest.raises(NotFoundError):
        await storage.stat(ref.key)


async def test_probe_reports_bucket_state(clients: Callable[..., Awaitable[Any]]) -> None:
    client, cfg = await clients()

    with pytest.raises(StorageError) as excinfo:
        await s3kit.probe(client, cfg).run()
    assert cfg.bucket in str(excinfo.value)

    await client.create_bucket(Bucket=cfg.bucket)
    await s3kit.probe(client, cfg).run()


async def test_create_bucket_on_start(clients: Callable[..., Awaitable[Any]]) -> None:
    client, cfg = await clients(create_bucket=True, probe_on_start=True)

    await s3kit.ensure_bucket(client, cfg, nop())
    # Рестарт с тем же бакетом — обычный случай, существующий бакет не ошибка.
    await s3kit.ensure_bucket(client, cfg, nop())

    await s3kit.check_bucket(client, cfg.bucket)


async def test_provider_graph_against_live_storage(
    clients: Callable[..., Awaitable[Any]],
) -> None:
    _, cfg = await clients(create_bucket=True, probe_on_start=True)

    class _Deps(Provider):
        scope = Scope.APP

        @provide
        def config(self) -> Config:
            return cfg

        @provide
        def log(self) -> Logger:
            return nop()

    container = make_async_container(StoragekitProvider(), s3kit.S3Provider(), _Deps())
    try:
        storage = await container.get(Storage)
        ref = await storage.put(b"through-di", "text/plain", 10)
        assert (await storage.stat(ref.key)).size == 10
    finally:
        await container.close()


async def test_presign_round_trip(storages: StorageFactory) -> None:
    storage, _, _ = await storages()
    body = b"presigned-body"
    key = "uploads/report.pdf"

    upload = await storage.presign_put(key, 300)
    async with httpx.AsyncClient() as http:
        put = await http.put(upload, content=body)
        assert put.status_code == 200

        link = await storage.presign_get(
            key,
            300,
            response_content_disposition='attachment; filename="report.pdf"',
            response_content_type="application/pdf",
        )
        got = await http.get(link)

    assert got.status_code == 200
    assert got.content == body
    assert got.headers["content-disposition"] == 'attachment; filename="report.pdf"'
    assert got.headers["content-type"] == "application/pdf"


async def test_expired_presigned_link_is_rejected(storages: StorageFactory) -> None:
    # Дедлайн ссылки считает само хранилище: подменить его часы нечем, поэтому
    # тест ждёт минимальный TTL (1 s) с запасом на планировщик.
    storage, _, _ = await storages()
    ref = await storage.put(b"payload", "text/plain", 7)

    link = await storage.presign_get(ref.key, MIN_PRESIGN_TTL)
    async with httpx.AsyncClient() as http:
        assert (await http.get(link)).status_code == 200
        await asyncio.sleep(MIN_PRESIGN_TTL + 1.0)
        expired = await http.get(link)

    assert expired.status_code == 403
    assert "expired" in expired.text.lower()


async def test_chain_credentials_read_the_environment(
    minio: MinioFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", minio.access_key)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", minio.secret_key)
    cfg = minio.config(
        f"test-bucket-{next(_BUCKETS)}", credentials="chain", access_key="", secret_key=""
    )

    async with s3kit.new_client(cfg) as client:
        await client.create_bucket(Bucket=cfg.bucket)
        storage = s3kit.Storage(client, cfg, None, nop())
        ref = await storage.put(b"chain", "text/plain", 5)

        assert (await storage.stat(ref.key)).size == 5


async def test_metrics_are_emitted_for_live_operations(
    storages: StorageFactory, read_metric: Callable[..., float]
) -> None:
    storage, _, _ = await storages()

    ref = await storage.put(b"m", "image/png", 1)
    reader, _ = await storage.get(ref.key)
    await reader.aclose()
    await storage.delete(ref.key)

    assert read_metric("storage_ops_total", {"op": "put", "result": "ok"}) == 1
    assert read_metric("storage_op_duration_seconds", {"op": "get"}) == 1
