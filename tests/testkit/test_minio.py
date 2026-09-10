"""start_minio: живой контейнер, его креды и storagekit.Config к нему."""

import pytest
from docker.errors import NotFound

from pylibinfra.logkit import nop
from pylibinfra.storagekit import (
    BUCKET_LOOKUP_PATH,
    CREDENTIALS_STATIC,
    DEFAULT_PART_SIZE,
    URL_STYLE_PATH,
)
from pylibinfra.storagekit import s3 as s3kit
from pylibinfra.testkit import MINIO_IMAGE, start_minio
from pylibinfra.testkit import minio as testkit_minio

pytestmark = pytest.mark.integration


async def test_start_minio_yields_endpoint_and_credentials() -> None:
    async with start_minio() as minio:
        host, _, port = minio.endpoint.rpartition(":")

        assert host
        assert port.isdigit()
        assert "://" not in minio.endpoint
        assert minio.access_key
        assert minio.secret_key
        assert minio.container.get_config()["endpoint"] == minio.endpoint

    stopped = minio.container.get_wrapped_container()
    try:
        stopped.reload()
    except NotFound:
        return
    assert stopped.status != "running"


async def test_config_points_at_the_container_and_works() -> None:
    async with start_minio(image=MINIO_IMAGE) as minio:
        cfg = minio.config("fixture-bucket")

        assert cfg.endpoint == minio.endpoint
        assert cfg.bucket == "fixture-bucket"
        assert cfg.credentials == CREDENTIALS_STATIC
        assert cfg.bucket_lookup == BUCKET_LOOKUP_PATH
        assert cfg.url_style == URL_STYLE_PATH
        assert cfg.part_size == DEFAULT_PART_SIZE
        assert cfg.probe_on_start is False
        assert cfg.secret_key.get_secret_value() == minio.secret_key

        async with s3kit.new_client(cfg) as client:
            await client.create_bucket(Bucket=cfg.bucket)
            storage = s3kit.Storage(client, cfg, None, nop())

            ref = await storage.put(b"fixture", "text/plain", 7)

            assert (await storage.stat(ref.key)).size == 7


async def test_failed_start_still_stops_the_container(monkeypatch: pytest.MonkeyPatch) -> None:
    # Сорвавшееся ожидание готовности не должно оставлять контейнер в демоне.
    stopped: list[int] = []

    class _Broken:
        def __init__(self, image: str) -> None:
            self.image = image

        def start(self) -> None:
            raise RuntimeError("wait strategy timed out")

        def stop(self) -> None:
            stopped.append(1)

    monkeypatch.setattr(testkit_minio, "MinioContainer", _Broken)

    with pytest.raises(RuntimeError, match="wait strategy"):
        async with start_minio():
            pass

    assert stopped == [1]


async def test_config_overrides_win(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_S3_BUCKET", "from-env")
    monkeypatch.setenv("STORAGE_PUBLIC_BASE_URL", "https://leaked.example.com")

    async with start_minio() as minio:
        cfg = minio.config("bucket", probe_on_start=True, max_retries=0)

        assert cfg.bucket == "bucket"
        assert cfg.public_base_url == ""
        assert cfg.probe_on_start is True
        assert cfg.max_retries == 0
