"""Адаптер s3.Storage без сети: маппинг ошибок, контракт size, presign и метрики."""

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from botocore.exceptions import ClientError

from pylibinfra.logkit import nop
from pylibinfra.storagekit import (
    MIN_PART_SIZE,
    Config,
    ConfigError,
    DeleteFailedError,
    GetFailedError,
    NotFoundError,
    Presigner,
    PresignFailedError,
    SizeMismatchError,
    StatFailedError,
    Storage,
    StorageError,
    UploadFailedError,
    URLBuilder,
    new_config,
)
from pylibinfra.storagekit import s3 as s3kit
from pylibinfra.testkit import logtest

BUCKET = "media"


def unit_config(**overrides: Any) -> Config:
    values: dict[str, Any] = {
        "endpoint": "s3.example.com:9000",
        "access_key": "key",
        "secret_key": "secret",
        "bucket": BUCKET,
        "region": "us-east-1",
        "probe_on_start": False,
    }
    values.update(overrides)
    return new_config(**values)


def client_error(code: str, status: int, operation: str = "GetObject") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        operation,
    )


class FakeClient:
    """S3-клиент в памяти: пишет вызовы в `calls`, отвечает по заданному сценарию."""

    def __init__(self, error: Exception | None = None, fail_on: tuple[str, ...] = ()) -> None:
        self.error = error
        self.fail_on = fail_on
        self.calls: list[str] = []
        self.parts: list[bytes] = []
        self.objects: dict[str, bytes] = {}

    async def put_object(self, **kwargs: Any) -> Any:
        self.calls.append("put_object")
        self._maybe_raise("put_object")
        self.objects[str(kwargs["Key"])] = bytes(kwargs["Body"])
        return {"ETag": '"etag"'}

    async def get_object(self, **kwargs: Any) -> Any:
        self.calls.append("get_object")
        self._maybe_raise("get_object")
        return _response(self.objects[str(kwargs["Key"])])

    async def head_object(self, **kwargs: Any) -> Any:
        self.calls.append("head_object")
        self._maybe_raise("head_object")
        return _response(self.objects[str(kwargs["Key"])])

    async def delete_object(self, **kwargs: Any) -> Any:
        self.calls.append("delete_object")
        self._maybe_raise("delete_object")
        self.objects.pop(str(kwargs["Key"]), None)
        return {}

    async def create_multipart_upload(self, **kwargs: Any) -> Any:
        self.calls.append("create_multipart_upload")
        self._maybe_raise("create_multipart_upload")
        return {"UploadId": "upload-1"}

    async def upload_part(self, **kwargs: Any) -> Any:
        self.calls.append("upload_part")
        self._maybe_raise("upload_part")
        self.parts.append(bytes(kwargs["Body"]))
        return {"ETag": f'"part-{kwargs["PartNumber"]}"'}

    async def complete_multipart_upload(self, **kwargs: Any) -> Any:
        self.calls.append("complete_multipart_upload")
        self._maybe_raise("complete_multipart_upload")
        self.objects[str(kwargs["Key"])] = b"".join(self.parts)
        return {}

    async def abort_multipart_upload(self, **kwargs: Any) -> Any:
        self.calls.append("abort_multipart_upload")
        self._maybe_raise("abort_multipart_upload")
        return {}

    async def head_bucket(self, **kwargs: Any) -> Any:
        self.calls.append("head_bucket")
        self._maybe_raise("head_bucket")
        return {}

    async def create_bucket(self, **kwargs: Any) -> Any:
        self.calls.append("create_bucket")
        self._maybe_raise("create_bucket")
        return {}

    def _maybe_raise(self, op: str) -> None:
        if self.error is None:
            return
        if not self.fail_on or op in self.fail_on:
            raise self.error


def _response(data: bytes) -> dict[str, Any]:
    return {
        "Body": _Stream(data),
        "ContentLength": len(data),
        "ContentType": "application/octet-stream",
        "ETag": '"etag"',
        "LastModified": datetime.now(UTC),
    }


class _Stream:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.closed = False

    async def iter_chunks(self, size: int) -> Any:
        for start in range(0, len(self._data), size):
            yield self._data[start : start + size]

    def close(self) -> None:
        self.closed = True


class _SourceGoneError(Exception):
    pass


def _failing_body(*chunks: bytes) -> AsyncIterator[bytes]:
    async def source() -> AsyncIterator[bytes]:
        for chunk in chunks or (b"head",):
            yield chunk
        raise _SourceGoneError("source is gone")

    return source()


def new_storage(client: FakeClient, **overrides: Any) -> s3kit.Storage:
    cfg = unit_config(**overrides)
    return s3kit.Storage(
        client,  # type: ignore[arg-type]
        cfg,
        URLBuilder(cfg.public_base_url, cfg.bucket, cfg.url_style),
        nop(),
    )


def test_adapter_satisfies_ports() -> None:
    storage = new_storage(FakeClient())

    assert isinstance(storage, Storage)
    assert isinstance(storage, Presigner)


@pytest.mark.parametrize("code", ["NoSuchKey", "404", "NotFound"])
async def test_missing_object_maps_to_not_found(code: str) -> None:
    storage = new_storage(FakeClient(client_error(code, 404)))

    with pytest.raises(NotFoundError):
        await storage.get("k.bin")
    with pytest.raises(NotFoundError):
        await storage.stat("k.bin")


async def test_not_found_is_also_operation_failure() -> None:
    storage = new_storage(FakeClient(client_error("NoSuchKey", 404)))

    with pytest.raises(GetFailedError):
        await storage.get("k.bin")
    with pytest.raises(StatFailedError):
        await storage.stat("k.bin")


async def test_storage_failure_is_not_not_found() -> None:
    storage = new_storage(FakeClient(client_error("InternalError", 500)))

    with pytest.raises(GetFailedError) as get_error:
        await storage.get("k.bin")
    with pytest.raises(StatFailedError) as stat_error:
        await storage.stat("k.bin")
    with pytest.raises(DeleteFailedError):
        await storage.delete("k.bin")

    assert not isinstance(get_error.value, NotFoundError)
    assert not isinstance(stat_error.value, NotFoundError)


async def test_transport_failure_is_wrapped() -> None:
    storage = new_storage(FakeClient(OSError("connection reset")))

    with pytest.raises(GetFailedError):
        await storage.get("k.bin")
    with pytest.raises(StatFailedError):
        await storage.stat("k.bin")
    with pytest.raises(DeleteFailedError):
        await storage.delete("k.bin")
    with pytest.raises(UploadFailedError):
        await storage.put(b"x", "text/plain", 1)


@pytest.mark.parametrize(
    ("call", "error"),
    [
        ("get", GetFailedError),
        ("stat", StatFailedError),
        ("delete", DeleteFailedError),
    ],
)
async def test_empty_key_is_rejected_before_the_request(call: str, error: type[Exception]) -> None:
    client = FakeClient()
    storage = new_storage(client)
    method: Callable[[str], Any] = getattr(storage, call)

    with pytest.raises(error):
        await method("")

    assert client.calls == []


async def test_short_body_is_rejected_without_a_request() -> None:
    client = FakeClient()
    storage = new_storage(client)

    with pytest.raises(SizeMismatchError):
        await storage.put(b"0123456789", "application/pdf", 4)
    with pytest.raises(SizeMismatchError):
        await storage.put(b"0123456789", "application/pdf", 40)

    assert client.calls == []


async def test_multipart_mismatch_aborts_the_upload() -> None:
    client = FakeClient()
    storage = new_storage(client, part_size=MIN_PART_SIZE)
    body = b"z" * (MIN_PART_SIZE + 16)

    with pytest.raises(SizeMismatchError):
        await storage.put(body, "application/octet-stream", MIN_PART_SIZE + 32)

    assert "abort_multipart_upload" in client.calls
    assert "complete_multipart_upload" not in client.calls
    assert client.objects == {}


async def test_multipart_upload_sends_parts_of_part_size() -> None:
    client = FakeClient()
    storage = new_storage(client, part_size=MIN_PART_SIZE)
    body = b"z" * (MIN_PART_SIZE + 16)

    ref = await storage.put(body, "application/octet-stream", len(body))

    assert ref.size == len(body)
    assert [len(part) for part in client.parts] == [MIN_PART_SIZE, 16]
    assert client.objects[ref.key] == body


async def test_unknown_size_sends_one_request_for_a_small_body() -> None:
    client = FakeClient()
    storage = new_storage(client)

    ref = await storage.put(b"small", "text/plain", -1)

    assert ref.size == 5
    assert client.calls == ["put_object"]


async def test_unknown_size_streams_multipart_for_a_large_body() -> None:
    client = FakeClient()
    storage = new_storage(client, part_size=MIN_PART_SIZE)
    body = b"y" * (2 * MIN_PART_SIZE + 7)

    ref = await storage.put(body, "application/octet-stream", -1)

    assert ref.size == len(body)
    assert [len(part) for part in client.parts] == [MIN_PART_SIZE, MIN_PART_SIZE, 7]
    assert client.calls.count("complete_multipart_upload") == 1


async def test_get_closes_the_stream_on_reader_close() -> None:
    client = FakeClient()
    storage = new_storage(client)
    ref = await storage.put(b"payload", "text/plain", 7)

    reader, info = await storage.get(ref.key)
    async with reader:
        assert await reader.read() == b"payload"

    assert info.etag == "etag"
    assert info.size == 7


async def test_incomplete_response_is_an_operation_failure() -> None:
    # S3-совместимый сервер без Last-Modified не должен ронять кит `KeyError`'ом.
    client = FakeClient()
    storage = new_storage(client)
    ref = await storage.put(b"payload", "text/plain", 7)
    stored = client.objects[ref.key]

    async def truncated(**kwargs: Any) -> Any:
        response = _response(stored)
        del response["LastModified"]
        return response

    client.get_object = truncated  # type: ignore[method-assign]
    client.head_object = truncated  # type: ignore[method-assign]

    with pytest.raises(GetFailedError, match="incomplete response"):
        await storage.get(ref.key)
    with pytest.raises(StatFailedError, match="incomplete response"):
        await storage.stat(ref.key)


async def test_incomplete_get_response_releases_the_stream() -> None:
    client = FakeClient()
    storage = new_storage(client)
    ref = await storage.put(b"payload", "text/plain", 7)
    broken = _response(client.objects[ref.key])
    del broken["LastModified"]

    async def truncated(**kwargs: Any) -> Any:
        return broken

    client.get_object = truncated  # type: ignore[method-assign]

    with pytest.raises(GetFailedError):
        await storage.get(ref.key)

    assert broken["Body"].closed is True


async def test_ref_url_without_url_builder_is_the_key() -> None:
    client = FakeClient()
    storage = s3kit.Storage(client, unit_config(), None, None)  # type: ignore[arg-type]

    ref = await storage.put(b"x", "image/png", 1)

    assert ref.url == ref.key


async def test_presign_get_signs_response_headers() -> None:
    cfg = unit_config()
    async with s3kit.new_client(cfg) as client:
        storage = s3kit.Storage(client, cfg, URLBuilder("", cfg.bucket), nop())

        link = await storage.presign_get(
            "reports/2026-07.pdf",
            3600,
            response_content_disposition='attachment; filename="report.pdf"',
            response_content_type="application/pdf",
        )

    query = parse_qs(urlparse(link).query)
    assert query["response-content-disposition"] == ['attachment; filename="report.pdf"']
    assert query["response-content-type"] == ["application/pdf"]
    assert "host" in query["X-Amz-SignedHeaders"][0]
    assert query["X-Amz-Expires"] == ["3600"]
    assert "reports/2026-07.pdf" in link
    assert BUCKET in link


async def test_presign_put_is_signed() -> None:
    cfg = unit_config()
    async with s3kit.new_client(cfg) as client:
        storage = s3kit.Storage(client, cfg, URLBuilder("", cfg.bucket), nop())

        link = await storage.presign_put("uploads/new.bin", 3600)

    assert "X-Amz-Signature" in link
    assert "uploads/new.bin" in link


@pytest.mark.parametrize("ttl", [0.0, 0.5, 8 * 24 * 60 * 60])
async def test_presign_rejects_ttl_outside_the_range(ttl: float) -> None:
    storage = new_storage(FakeClient())

    with pytest.raises(PresignFailedError):
        await storage.presign_get("k.bin", ttl)
    with pytest.raises(PresignFailedError):
        await storage.presign_put("k.bin", ttl)


async def test_presign_rejects_empty_key() -> None:
    storage = new_storage(FakeClient())

    with pytest.raises(PresignFailedError):
        await storage.presign_get("", 60)
    with pytest.raises(PresignFailedError):
        await storage.presign_put("", 60)


async def test_presign_failure_is_wrapped() -> None:
    storage = new_storage(FakeClient())

    with pytest.raises(PresignFailedError):
        await storage.presign_get("k.bin", 60)


async def test_metrics_count_operations(read_metric: Callable[..., float]) -> None:
    client = FakeClient()
    storage = new_storage(client)

    ref = await storage.put(b"m", "image/png", 1)
    reader, _ = await storage.get(ref.key)
    await reader.aclose()
    await storage.stat(ref.key)
    await storage.delete(ref.key)
    with pytest.raises(SizeMismatchError):
        await storage.put(b"m", "image/png", 2)

    assert read_metric("storage_ops_total", {"op": "put", "result": "ok"}) == 1
    assert read_metric("storage_ops_total", {"op": "get", "result": "ok"}) == 1
    assert read_metric("storage_ops_total", {"op": "stat", "result": "ok"}) == 1
    assert read_metric("storage_ops_total", {"op": "delete", "result": "ok"}) == 1
    assert read_metric("storage_ops_total", {"op": "put", "result": "error"}) == 1
    assert read_metric("storage_op_duration_seconds", {"op": "put"}) == 2
    assert read_metric("storage_op_duration_seconds", {"op": "get"}) == 1


async def test_presign_metrics(read_metric: Callable[..., float]) -> None:
    cfg = unit_config()
    async with s3kit.new_client(cfg) as client:
        storage = s3kit.Storage(client, cfg, None, nop())
        await storage.presign_get("k.bin", 60)
        await storage.presign_put("k.bin", 60)

    assert read_metric("storage_ops_total", {"op": "presign_get", "result": "ok"}) == 1
    assert read_metric("storage_ops_total", {"op": "presign_put", "result": "ok"}) == 1
    assert read_metric("storage_op_duration_seconds", {"op": "presign_get"}) == 1


async def test_client_builds_for_every_credentials_and_lookup_mode() -> None:
    for credentials in ("static", "chain"):
        for lookup in ("auto", "path", "dns"):
            cfg = unit_config(
                credentials=credentials,
                bucket_lookup=lookup,
                access_key="" if credentials == "chain" else "key",
                secret_key="" if credentials == "chain" else "secret",
            )
            async with s3kit.new_client(cfg) as client:
                assert client is not None


async def test_anonymous_client_is_unsigned() -> None:
    cfg = unit_config(access_key="", secret_key="")

    async with s3kit.new_client(cfg) as client:
        storage = s3kit.Storage(client, cfg, None, nop())
        link = await storage.presign_get("k.bin", 60)

    assert "X-Amz-Signature" not in link


async def test_body_longer_than_declared_at_the_part_boundary() -> None:
    client = FakeClient()
    storage = new_storage(client, part_size=MIN_PART_SIZE)

    with pytest.raises(SizeMismatchError):
        await storage.put(b"b" * (MIN_PART_SIZE + 5), "application/octet-stream", MIN_PART_SIZE)

    assert client.calls == []


async def test_multipart_body_longer_inside_a_part() -> None:
    client = FakeClient()
    storage = new_storage(client, part_size=MIN_PART_SIZE)

    with pytest.raises(SizeMismatchError):
        await storage.put(
            b"b" * (MIN_PART_SIZE + 32), "application/octet-stream", MIN_PART_SIZE + 16
        )

    assert "abort_multipart_upload" in client.calls
    assert "complete_multipart_upload" not in client.calls


async def test_multipart_body_longer_after_the_last_part() -> None:
    client = FakeClient()
    storage = new_storage(client, part_size=MIN_PART_SIZE)

    with pytest.raises(SizeMismatchError):
        await storage.put(
            b"b" * (2 * MIN_PART_SIZE + 8), "application/octet-stream", 2 * MIN_PART_SIZE
        )

    assert "abort_multipart_upload" in client.calls
    assert "complete_multipart_upload" not in client.calls


@pytest.mark.parametrize(
    "failing_op", ["create_multipart_upload", "upload_part", "complete_multipart_upload"]
)
async def test_multipart_failure_is_wrapped_and_aborted(failing_op: str) -> None:
    client = FakeClient(client_error("InternalError", 500), fail_on=(failing_op,))
    storage = new_storage(client, part_size=MIN_PART_SIZE)
    body = b"b" * (MIN_PART_SIZE + 16)

    with pytest.raises(UploadFailedError):
        await storage.put(body, "application/octet-stream", len(body))

    if failing_op != "create_multipart_upload":
        assert "abort_multipart_upload" in client.calls


async def test_failed_abort_does_not_mask_the_upload_error() -> None:
    client = FakeClient(
        client_error("InternalError", 500), fail_on=("upload_part", "abort_multipart_upload")
    )
    storage = new_storage(client, part_size=MIN_PART_SIZE)
    body = b"b" * (MIN_PART_SIZE + 16)

    with pytest.raises(UploadFailedError):
        await storage.put(body, "application/octet-stream", len(body))

    assert "abort_multipart_upload" in client.calls


async def test_failing_body_is_wrapped_before_the_first_request() -> None:
    client = FakeClient()
    storage = new_storage(client)

    with pytest.raises(UploadFailedError) as excinfo:
        await storage.put(_failing_body(), "application/octet-stream", 64)

    assert isinstance(excinfo.value.__cause__, _SourceGoneError)
    assert client.calls == []


async def test_failing_body_at_the_part_boundary_is_wrapped() -> None:
    # Часть набралась ровно, а отказ источника ждёт на проверке хвоста.
    client = FakeClient()
    storage = new_storage(client, part_size=MIN_PART_SIZE)

    with pytest.raises(UploadFailedError) as excinfo:
        await storage.put(_failing_body(b"b" * MIN_PART_SIZE), "text/plain", MIN_PART_SIZE)

    assert isinstance(excinfo.value.__cause__, _SourceGoneError)
    assert client.calls == []


async def test_cancelled_cleanup_is_logged_and_propagated() -> None:
    log = logtest.Capture()
    client = FakeClient(client_error("InternalError", 500), fail_on=("upload_part",))
    storage = s3kit.Storage(
        client,  # type: ignore[arg-type]
        unit_config(part_size=MIN_PART_SIZE),
        None,
        log,
    )

    async def cancelled_abort(**kwargs: Any) -> Any:
        client.calls.append("abort_multipart_upload")
        raise asyncio.CancelledError

    client.abort_multipart_upload = cancelled_abort  # type: ignore[method-assign]
    body = b"b" * (MIN_PART_SIZE + 16)

    with pytest.raises(asyncio.CancelledError):
        await storage.put(body, "application/octet-stream", len(body))

    assert log.find("storagekit: rejected upload left parts behind") is not None


async def test_failing_body_aborts_a_started_multipart() -> None:
    client = FakeClient()
    storage = new_storage(client, part_size=MIN_PART_SIZE)

    # Тело переваливает за одну часть, поэтому загрузка успевает начаться.
    body = _failing_body(b"b" * MIN_PART_SIZE, b"tail")

    with pytest.raises(UploadFailedError):
        await storage.put(body, "application/octet-stream", -1)

    assert "abort_multipart_upload" in client.calls
    assert "complete_multipart_upload" not in client.calls
    assert client.objects == {}


async def test_unknown_size_multipart_failure_is_aborted() -> None:
    client = FakeClient(client_error("InternalError", 500), fail_on=("upload_part",))
    storage = new_storage(client, part_size=MIN_PART_SIZE)

    with pytest.raises(UploadFailedError):
        await storage.put(b"b" * (MIN_PART_SIZE + 16), "application/octet-stream", -1)

    assert "abort_multipart_upload" in client.calls


async def test_check_bucket_reports_a_missing_bucket() -> None:
    client = FakeClient(client_error("NoSuchBucket", 404, "HeadBucket"))

    with pytest.raises(StorageError) as excinfo:
        await s3kit.check_bucket(client, BUCKET)  # type: ignore[arg-type]

    assert "not found" in str(excinfo.value)


async def test_check_bucket_reports_a_denied_bucket() -> None:
    client = FakeClient(client_error("AccessDenied", 403, "HeadBucket"))

    with pytest.raises(StorageError) as excinfo:
        await s3kit.check_bucket(client, BUCKET)  # type: ignore[arg-type]

    assert "bucket exists check" in str(excinfo.value)


async def test_ensure_bucket_creates_a_missing_bucket() -> None:
    client = FakeClient(client_error("NoSuchBucket", 404, "HeadBucket"), fail_on=("head_bucket",))
    cfg = unit_config(create_bucket=True, probe_on_start=False, probe_timeout=0.0)

    await s3kit.ensure_bucket(client, cfg, nop())  # type: ignore[arg-type]

    assert "create_bucket" in client.calls


async def test_ensure_bucket_keeps_an_existing_bucket() -> None:
    client = FakeClient()
    cfg = unit_config(create_bucket=True, probe_on_start=True)

    await s3kit.ensure_bucket(client, cfg, nop())  # type: ignore[arg-type]

    assert "create_bucket" not in client.calls


async def test_ensure_bucket_tolerates_a_concurrent_creation() -> None:
    client = FakeClient(fail_on=("head_bucket", "create_bucket"))
    client.error = client_error("NoSuchBucket", 404, "HeadBucket")
    cfg = unit_config(create_bucket=True, probe_on_start=False)

    async def create_bucket(**kwargs: Any) -> Any:
        client.calls.append("create_bucket")
        raise client_error("BucketAlreadyOwnedByYou", 409, "CreateBucket")

    client.create_bucket = create_bucket  # type: ignore[method-assign]

    await s3kit.ensure_bucket(client, cfg, nop())  # type: ignore[arg-type]

    assert "create_bucket" in client.calls


@pytest.mark.parametrize(
    "error",
    [client_error("AccessDenied", 403, "CreateBucket"), OSError("connection reset")],
)
async def test_ensure_bucket_reports_a_failed_creation(error: Exception) -> None:
    client = FakeClient(client_error("NoSuchBucket", 404, "HeadBucket"), fail_on=("head_bucket",))
    cfg = unit_config(create_bucket=True, probe_on_start=False)

    async def create_bucket(**kwargs: Any) -> Any:
        raise error

    client.create_bucket = create_bucket  # type: ignore[method-assign]

    with pytest.raises(StorageError) as excinfo:
        await s3kit.ensure_bucket(client, cfg, nop())  # type: ignore[arg-type]

    assert "create bucket" in str(excinfo.value)


async def test_ensure_bucket_reports_a_failed_existence_check() -> None:
    client = FakeClient(client_error("AccessDenied", 403, "HeadBucket"))
    cfg = unit_config(create_bucket=True, probe_on_start=False)

    with pytest.raises(StorageError) as excinfo:
        await s3kit.ensure_bucket(client, cfg, nop())  # type: ignore[arg-type]

    assert "bucket exists check" in str(excinfo.value)


async def test_ensure_bucket_reports_a_transport_failure() -> None:
    client = FakeClient(OSError("connection reset"))
    cfg = unit_config(create_bucket=True, probe_on_start=False)

    with pytest.raises(StorageError):
        await s3kit.ensure_bucket(client, cfg, nop())  # type: ignore[arg-type]


async def test_ensure_bucket_skips_disabled_checks() -> None:
    client = FakeClient(OSError("must not be called"))
    cfg = unit_config(create_bucket=False, probe_on_start=False)

    await s3kit.ensure_bucket(client, cfg, nop())  # type: ignore[arg-type]

    assert client.calls == []


async def test_new_client_wraps_a_broken_session(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> Any:
        raise RuntimeError("no session")

    monkeypatch.setattr(s3kit.client.aioboto3, "Session", boom)

    with pytest.raises(ConfigError):
        async with s3kit.new_client(unit_config()):
            pass
