"""Боевой S3-совместимый адаптер Storage поверх aioboto3 (MinIO через
endpoint_url). Доменно-нейтрален: пишет байты как есть, image/file-валидация
(mime, размеры, декод) — на стороне приложения.
"""

from typing import Any

from botocore.exceptions import ClientError

from pylibinfra.storagekit.errors import ErrNotFound, ErrUploadFailed
from pylibinfra.storagekit.storage import Ref, new_key
from pylibinfra.storagekit.url_builder import URLBuilder


class Uploader:
    # client — async S3-клиент aioboto3 (session.client("s3", ...) внутри
    # async-with); под mypy всплывает как Any, т.к. types_aiobotocore_s3 не стоит.
    def __init__(self, client: Any, bucket: str, urls: URLBuilder) -> None:
        self._client = client
        self._bucket = bucket
        self._urls = urls

    async def put(self, data: bytes, content_type: str) -> Ref:
        key = new_key(content_type)
        try:
            await self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        except ClientError as exc:
            raise ErrUploadFailed(f"put object: {exc}") from exc
        return Ref(
            key=key,
            url=self._urls.build(key),
            content_type=content_type,
            size=len(data),
        )

    async def delete(self, key: str) -> None:
        try:
            await self._client.delete_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404"):
                raise ErrNotFound(key) from exc
            raise ErrUploadFailed(f"remove object {key!r}: {exc}") from exc
