"""s3 — боевой S3-совместимый адаптер storagekit.Storage поверх aioboto3."""

from .module import new_s3_client, new_uploader
from .uploader import Uploader

__all__ = ["Uploader", "new_s3_client", "new_uploader"]
