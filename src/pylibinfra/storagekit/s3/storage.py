"""Боевой адаптер `storagekit.Storage`/`Presigner` поверх aioboto3 (S3, MinIO)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter
from typing import Any

from botocore.exceptions import ClientError

from pylibinfra._internal import otelkit
from pylibinfra.logkit import Logger, nop
from pylibinfra.storagekit.body import Body, PartStream
from pylibinfra.storagekit.config import DEFAULT_PART_SIZE, Config
from pylibinfra.storagekit.errors import (
    DeleteFailedError,
    GetFailedError,
    NotFoundError,
    PresignFailedError,
    SizeMismatchError,
    StatFailedError,
    StorageError,
    UploadFailedError,
)
from pylibinfra.storagekit.storage import (
    ObjectInfo,
    ObjectReader,
    Ref,
    new_key,
    normalize_content_type,
    validate_presign_ttl,
)
from pylibinfra.storagekit.url_builder import URLBuilder, escape_key

from .client import Client

_KIT = "storagekit"

# Окно на снятие начатой multipart-загрузки: тело уже отвергнуто, ждать
# хранилище дольше нечего.
_CLEANUP_TIMEOUT = 5.0
_READ_CHUNK = 64 * 1024


class Storage:
    """Адаптер `storagekit.Storage` и `storagekit.Presigner` поверх S3.

    Память на `put` ограничена частью `STORAGE_S3_PART_SIZE`: тело целиком не
    буферизуется. Эмитит `storage_ops_total` и `storage_op_duration_seconds`.
    """

    def __init__(
        self,
        client: Client,
        cfg: Config,
        urls: URLBuilder | None = None,
        log: Logger | None = None,
    ) -> None:
        self._client = client
        self._bucket = cfg.bucket
        # Config, собранный мимо валидации, не должен уводить размер части в 0.
        self._part_size = cfg.part_size or DEFAULT_PART_SIZE
        self._urls = urls
        self._log = log or nop()
        self._metrics = _Metrics()

    async def put(self, body: Body, content_type: str, size: int) -> Ref:
        """Стримит тело под новым ключом; расхождение с `size` — `SizeMismatchError` без объекта."""
        with self._observe("put"):
            content_type = normalize_content_type(content_type)
            key = new_key(content_type)
            stream = PartStream(body, self._part_size)
            if size < 0:
                stored = await self._put_unknown_size(key, content_type, stream)
            elif size <= self._part_size:
                stored = await self._put_single(key, content_type, stream, size)
            else:
                stored = await self._put_multipart(key, content_type, stream, size)
            return Ref(key=key, url=self._url(key), content_type=content_type, size=stored)

    async def get(self, key: str) -> tuple[ObjectReader, ObjectInfo]:
        """Открывает объект; ридер закрывает вызывающий, отсутствующий ключ — `NotFoundError`."""
        with self._observe("get"):
            if not key:
                raise GetFailedError("storagekit/s3: get: key required")
            response = await self._call("get", key, GetFailedError, self._client.get_object)
            stream = response["Body"]
            try:
                info = _object_info(response, "get", key, GetFailedError)
            except GetFailedError:
                # Тело ответа держит соединение до хранилища и без ридера.
                stream.close()
                raise

            async def close() -> None:
                stream.close()

            return ObjectReader(stream.iter_chunks(_READ_CHUNK), close=close), info

    async def stat(self, key: str) -> ObjectInfo:
        """Метаданные объекта; отсутствующий ключ — `NotFoundError`."""
        with self._observe("stat"):
            if not key:
                raise StatFailedError("storagekit/s3: stat: key required")
            response = await self._call("stat", key, StatFailedError, self._client.head_object)
            return _object_info(response, "stat", key, StatFailedError)

    async def delete(self, key: str) -> None:
        """Идемпотентен: S3 отвечает 204 и на отсутствующий ключ."""
        with self._observe("delete"):
            if not key:
                raise DeleteFailedError("storagekit/s3: delete: key required")
            try:
                await self._client.delete_object(Bucket=self._bucket, Key=key)
            except Exception as exc:
                raise DeleteFailedError(
                    f"storagekit/s3: delete: remove object {key!r}: {exc}"
                ) from exc

    async def presign_get(
        self,
        key: str,
        ttl: float,
        *,
        response_content_disposition: str | None = None,
        response_content_type: str | None = None,
    ) -> str:
        """Подписывает GET-ссылку на объект сконфигуренного бакета."""
        with self._observe("presign_get"):
            params = self._presign_params(key, ttl)
            if response_content_disposition:
                params["ResponseContentDisposition"] = response_content_disposition
            if response_content_type:
                params["ResponseContentType"] = response_content_type
            return await self._presign("get_object", key, params, ttl)

    async def presign_put(self, key: str, ttl: float) -> str:
        """Подписывает PUT-ссылку: клиент загружает объект напрямую в S3."""
        with self._observe("presign_put"):
            return await self._presign("put_object", key, self._presign_params(key, ttl), ttl)

    async def _put_single(self, key: str, content_type: str, stream: PartStream, size: int) -> int:
        data = await _next_part(stream)
        if len(data) != size:
            raise _size_mismatch(size, len(data))
        if await _has_more(stream):
            raise _body_longer(size)
        await self._put_object(key, content_type, data)
        return len(data)

    async def _put_multipart(
        self, key: str, content_type: str, stream: PartStream, size: int
    ) -> int:
        upload_id = await self._create_multipart(key, content_type)
        parts: list[dict[str, Any]] = []
        sent = 0
        try:
            while sent < size:
                part = await _next_part(stream)
                if not part:
                    break
                if sent + len(part) > size:
                    raise _body_longer(size)
                parts.append(await self._upload_part(key, upload_id, len(parts) + 1, part))
                sent += len(part)
            if sent != size:
                raise _size_mismatch(size, sent)
            if await _has_more(stream):
                raise _body_longer(size)
            await self._complete_multipart(key, upload_id, parts)
        except BaseException:
            await self._abort_multipart(key, upload_id)
            raise
        return sent

    async def _put_unknown_size(self, key: str, content_type: str, stream: PartStream) -> int:
        head = await _next_part(stream)
        if not await _has_more(stream):
            await self._put_object(key, content_type, head)
            return len(head)

        upload_id = await self._create_multipart(key, content_type)
        parts: list[dict[str, Any]] = []
        sent = 0
        try:
            part = head
            while part:
                parts.append(await self._upload_part(key, upload_id, len(parts) + 1, part))
                sent += len(part)
                part = await _next_part(stream)
            await self._complete_multipart(key, upload_id, parts)
        except BaseException:
            await self._abort_multipart(key, upload_id)
            raise
        return sent

    async def _put_object(self, key: str, content_type: str, data: bytes) -> None:
        try:
            await self._client.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
            )
        except Exception as exc:
            raise UploadFailedError(f"storagekit/s3: put: put object {key!r}: {exc}") from exc

    async def _create_multipart(self, key: str, content_type: str) -> str:
        try:
            response = await self._client.create_multipart_upload(
                Bucket=self._bucket, Key=key, ContentType=content_type
            )
        except Exception as exc:
            raise UploadFailedError(
                f"storagekit/s3: put: create multipart upload {key!r}: {exc}"
            ) from exc
        return str(response["UploadId"])

    async def _upload_part(
        self, key: str, upload_id: str, number: int, data: bytes
    ) -> dict[str, Any]:
        try:
            response = await self._client.upload_part(
                Bucket=self._bucket,
                Key=key,
                UploadId=upload_id,
                PartNumber=number,
                Body=data,
            )
        except Exception as exc:
            raise UploadFailedError(
                f"storagekit/s3: put: upload part {number} of {key!r}: {exc}"
            ) from exc
        return {"ETag": response["ETag"], "PartNumber": number}

    async def _complete_multipart(
        self, key: str, upload_id: str, parts: list[dict[str, Any]]
    ) -> None:
        try:
            await self._client.complete_multipart_upload(
                Bucket=self._bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
        except Exception as exc:
            raise UploadFailedError(
                f"storagekit/s3: put: complete multipart upload {key!r}: {exc}"
            ) from exc

    async def _abort_multipart(self, key: str, upload_id: str) -> None:
        try:
            async with asyncio.timeout(_CLEANUP_TIMEOUT):
                await self._client.abort_multipart_upload(
                    Bucket=self._bucket, Key=key, UploadId=upload_id
                )
        except BaseException as exc:
            self._log.error(
                "storagekit: rejected upload left parts behind",
                bucket=self._bucket,
                key=key,
                upload_id=upload_id,
                error=str(exc),
            )
            # Оставленные части стоят денег до lifecycle-правила бакета, поэтому
            # запись в лог идёт и на отмене — но отмену уборка не проглатывает.
            if not isinstance(exc, Exception):
                raise

    async def _call(
        self,
        op: str,
        key: str,
        failure: type[StorageError],
        method: Any,
    ) -> Any:
        try:
            return await method(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if _is_not_found(exc):
                raise NotFoundError(f"storagekit/s3: {op}: object {key!r} not found") from exc
            raise failure(f"storagekit/s3: {op}: {key!r}: {exc}") from exc
        except Exception as exc:
            raise failure(f"storagekit/s3: {op}: {key!r}: {exc}") from exc

    def _presign_params(self, key: str, ttl: float) -> dict[str, Any]:
        if not key:
            raise PresignFailedError("storagekit/s3: presign: key required")
        validate_presign_ttl(ttl)
        return {"Bucket": self._bucket, "Key": key}

    async def _presign(self, operation: str, key: str, params: dict[str, Any], ttl: float) -> str:
        try:
            link = await self._client.generate_presigned_url(
                operation, Params=params, ExpiresIn=int(ttl)
            )
        except Exception as exc:
            raise PresignFailedError(
                f"storagekit/s3: presign {operation}: {self._bucket!r}/{key!r}: {exc}"
            ) from exc
        return str(link)

    def _url(self, key: str) -> str:
        return escape_key(key) if self._urls is None else self._urls.build(key)

    @contextmanager
    def _observe(self, op: str) -> Iterator[None]:
        start = perf_counter()
        try:
            yield
        except BaseException:
            self._metrics.record(op, "error", perf_counter() - start)
            raise
        else:
            self._metrics.record(op, "ok", perf_counter() - start)


class _Metrics:
    """Инструменты кита; создаются на объект, чтобы тест видел их своим MeterProvider."""

    def __init__(self) -> None:
        meter = otelkit.meter(_KIT)
        self._ops = meter.create_counter(
            "storage_ops_total", description="Object storage operations by result"
        )
        self._duration = otelkit.duration_histogram(
            meter, "storage_op_duration_seconds", "Object storage operation duration"
        )

    def record(self, op: str, result: str, seconds: float) -> None:
        self._duration.record(seconds, {"op": op})
        self._ops.add(1, {"op": op, "result": result})


async def _next_part(stream: PartStream) -> bytes:
    """Следующая часть тела; отказ источника — `UploadFailedError`, как у `MemoryStorage`."""
    try:
        return await stream.next_part()
    except Exception as exc:
        raise UploadFailedError(f"storagekit/s3: put: read body: {exc}") from exc


async def _has_more(stream: PartStream) -> bool:
    """Остался ли в теле хотя бы байт; отказ источника — `UploadFailedError`."""
    try:
        return await stream.more()
    except Exception as exc:
        raise UploadFailedError(f"storagekit/s3: put: read body: {exc}") from exc


def _object_info(
    response: dict[str, Any], op: str, key: str, failure: type[StorageError]
) -> ObjectInfo:
    """Метаданные из ответа S3; неполный ответ — отказ операции, а не голый `KeyError`."""
    try:
        return ObjectInfo(
            size=int(response.get("ContentLength", 0)),
            content_type=str(response.get("ContentType", "")),
            etag=str(response.get("ETag", "")).strip('"'),
            last_modified=response["LastModified"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise failure(f"storagekit/s3: {op}: {key!r}: incomplete response: {exc}") from exc


def _is_not_found(exc: ClientError) -> bool:
    error = exc.response.get("Error", {})
    metadata = exc.response.get("ResponseMetadata", {})
    return int(metadata.get("HTTPStatusCode", 0)) == 404 or str(error.get("Code", "")) in (
        "404",
        "NoSuchKey",
        "NoSuchBucket",
        "NotFound",
    )


def _size_mismatch(declared: int, actual: int) -> SizeMismatchError:
    return SizeMismatchError(
        f"storagekit/s3: put: body size differs from declared size: "
        f"declared {declared}, actual {actual}"
    )


def _body_longer(declared: int) -> SizeMismatchError:
    return SizeMismatchError(
        f"storagekit/s3: put: body size differs from declared size: "
        f"declared {declared}, body is longer"
    )
