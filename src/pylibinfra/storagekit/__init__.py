"""storagekit — S3-совместимое объектное хранилище.

Порт `Storage` (put/get/stat/delete), presigned-ссылки, публичный URL и
in-memory адаптер; боевой S3-адаптер — в подпакете `storagekit.s3`. Он эмитит
`storage_ops_total` и `storage_op_duration_seconds`, спанов кит не заводит.
"""

from .body import Body
from .config import (
    BUCKET_LOOKUP_AUTO,
    BUCKET_LOOKUP_DNS,
    BUCKET_LOOKUP_PATH,
    CREDENTIALS_CHAIN,
    CREDENTIALS_STATIC,
    DEFAULT_PART_SIZE,
    DEFAULT_REGION,
    MAX_PART_SIZE,
    MIN_PART_SIZE,
    URL_STYLE_PATH,
    URL_STYLE_VIRTUAL,
    Config,
    new_config,
)
from .errors import (
    ConfigError,
    DeleteFailedError,
    GetFailedError,
    NotFoundError,
    PresignFailedError,
    SizeMismatchError,
    StatFailedError,
    StorageError,
    UploadFailedError,
)
from .memory import MemoryStorage
from .module import StoragekitProvider
from .storage import (
    DEFAULT_CONTENT_TYPE,
    MAX_PRESIGN_TTL,
    MIN_PRESIGN_TTL,
    ObjectInfo,
    ObjectReader,
    Presigner,
    Ref,
    Storage,
    new_key,
    normalize_content_type,
    validate_presign_ttl,
)
from .url_builder import URLBuilder

__all__ = [
    "BUCKET_LOOKUP_AUTO",
    "BUCKET_LOOKUP_DNS",
    "BUCKET_LOOKUP_PATH",
    "CREDENTIALS_CHAIN",
    "CREDENTIALS_STATIC",
    "DEFAULT_CONTENT_TYPE",
    "DEFAULT_PART_SIZE",
    "DEFAULT_REGION",
    "MAX_PART_SIZE",
    "MAX_PRESIGN_TTL",
    "MIN_PART_SIZE",
    "MIN_PRESIGN_TTL",
    "URL_STYLE_PATH",
    "URL_STYLE_VIRTUAL",
    "Body",
    "Config",
    "ConfigError",
    "DeleteFailedError",
    "GetFailedError",
    "MemoryStorage",
    "NotFoundError",
    "ObjectInfo",
    "ObjectReader",
    "PresignFailedError",
    "Presigner",
    "Ref",
    "SizeMismatchError",
    "StatFailedError",
    "Storage",
    "StorageError",
    "StoragekitProvider",
    "URLBuilder",
    "UploadFailedError",
    "new_config",
    "new_key",
    "normalize_content_type",
    "validate_presign_ttl",
]
