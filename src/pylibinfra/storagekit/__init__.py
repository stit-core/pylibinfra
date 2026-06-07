"""storagekit — S3-совместимое объектное хранилище.

Порт Storage (put/delete), стабильный публичный URL (URLBuilder) и in-memory
адаптер для тестов. Боевой S3-адаптер (aioboto3, MinIO через endpoint_url) — в
подпакете storagekit.s3. Kit доменно-нейтрален: image/file-семантика (валидация
mime, размеров, декод картинки) — на стороне приложения в доменных VO.
"""

from .config import Config
from .errors import ErrNotFound, ErrUploadFailed, StorageError
from .memory import MemoryStorage
from .module import StoragekitProvider, new_config
from .storage import Ref, Storage, new_key
from .url_builder import URLBuilder

__all__ = [
    "Config",
    "ErrNotFound",
    "ErrUploadFailed",
    "MemoryStorage",
    "Ref",
    "Storage",
    "StorageError",
    "StoragekitProvider",
    "URLBuilder",
    "new_config",
    "new_key",
]
