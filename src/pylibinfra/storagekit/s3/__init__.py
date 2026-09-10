"""s3 — боевой S3-совместимый адаптер storagekit поверх aioboto3 (S3, MinIO).

`Storage` реализует порты `storagekit.Storage`/`Presigner`, эмитит
`storage_ops_total` и `storage_op_duration_seconds`; `probe` — health-проба бакета.
"""

from .client import Client, new_client
from .module import PROBE_NAME, S3Provider, check_bucket, ensure_bucket, probe
from .storage import Storage

__all__ = [
    "PROBE_NAME",
    "Client",
    "S3Provider",
    "Storage",
    "check_bucket",
    "ensure_bucket",
    "new_client",
    "probe",
]
