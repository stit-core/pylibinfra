"""Изоляция тестов кита от STORAGE_*-переменных окружения разработчика."""

import pytest

_ENV = (
    "STORAGE_S3_ENDPOINT",
    "STORAGE_S3_ACCESS_KEY",
    "STORAGE_S3_SECRET_KEY",
    "STORAGE_S3_CREDENTIALS",
    "STORAGE_S3_BUCKET",
    "STORAGE_S3_REGION",
    "STORAGE_S3_USE_SSL",
    "STORAGE_S3_BUCKET_LOOKUP",
    "STORAGE_S3_CREATE_BUCKET",
    "STORAGE_S3_PART_SIZE",
    "STORAGE_S3_MAX_RETRIES",
    "STORAGE_S3_PROBE_TIMEOUT",
    "STORAGE_S3_PROBE_ON_START",
    "STORAGE_S3_URL_STYLE",
    "STORAGE_PUBLIC_BASE_URL",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
