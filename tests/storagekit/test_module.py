"""Проводка кита: URLBuilder из Config, граф боевого адаптера и стартовые проверки."""

from typing import Any

import pytest
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.logkit import Logger, nop
from pylibinfra.storagekit import (
    URL_STYLE_VIRTUAL,
    Config,
    Presigner,
    Storage,
    StorageError,
    StoragekitProvider,
    URLBuilder,
    new_config,
)
from pylibinfra.storagekit import s3 as s3kit


def offline_config(**overrides: Any) -> Config:
    values: dict[str, Any] = {
        "endpoint": "127.0.0.1:1",
        "access_key": "key",
        "secret_key": "secret",
        "bucket": "media",
        "max_retries": 0,
        "probe_timeout": 1.0,
        "probe_on_start": False,
        "public_base_url": "https://cdn.example.com",
    }
    values.update(overrides)
    return new_config(**values)


class _Deps(Provider):
    scope = Scope.APP

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self._cfg = cfg

    @provide
    def config(self) -> Config:
        return self._cfg

    @provide
    def log(self) -> Logger:
        return nop()


async def test_core_provider_gives_url_builder() -> None:
    container = make_async_container(StoragekitProvider(), _Deps(offline_config()))

    urls = await container.get(URLBuilder)

    assert urls.build("k.png") == "https://cdn.example.com/media/k.png"
    await container.close()


async def test_core_provider_honours_url_style() -> None:
    container = make_async_container(
        StoragekitProvider(),
        _Deps(offline_config(url_style=URL_STYLE_VIRTUAL, public_base_url="https://media.cdn.io")),
    )

    urls = await container.get(URLBuilder)

    assert urls.build("k.png") == "https://media.cdn.io/k.png"
    await container.close()


async def test_s3_provider_builds_graph_without_network() -> None:
    container = make_async_container(
        StoragekitProvider(), s3kit.S3Provider(), _Deps(offline_config())
    )

    storage = await container.get(Storage)
    presigner = await container.get(Presigner)
    adapter = await container.get(s3kit.Storage)
    client = await container.get(s3kit.Client)

    assert storage is presigner is adapter
    assert client is not None
    await container.close()


async def test_probe_on_start_fails_without_bucket() -> None:
    container = make_async_container(
        StoragekitProvider(), s3kit.S3Provider(), _Deps(offline_config(probe_on_start=True))
    )

    with pytest.raises(StorageError) as excinfo:
        await container.get(Storage)

    assert "bucket exists check" in str(excinfo.value)
    await container.close()


async def test_create_bucket_failure_fails_start() -> None:
    container = make_async_container(
        StoragekitProvider(),
        s3kit.S3Provider(),
        _Deps(offline_config(create_bucket=True, probe_on_start=False)),
    )

    with pytest.raises(StorageError):
        await container.get(Storage)

    await container.close()


async def test_startup_check_respects_probe_timeout() -> None:
    # TEST-NET-1: пакеты уходят в никуда, connect висит до дедлайна.
    container = make_async_container(
        StoragekitProvider(),
        s3kit.S3Provider(),
        _Deps(offline_config(endpoint="192.0.2.1:9000", probe_on_start=True, probe_timeout=0.5)),
    )

    with pytest.raises(StorageError) as excinfo:
        await container.get(Storage)

    assert "timed out" in str(excinfo.value)
    await container.close()


async def test_probe_is_named_storagekit() -> None:
    cfg = offline_config()
    async with s3kit.new_client(cfg) as client:
        probe = s3kit.probe(client, cfg)

        assert probe.name == s3kit.PROBE_NAME == "storagekit"


async def test_probe_reports_unreachable_storage() -> None:
    cfg = offline_config()
    async with s3kit.new_client(cfg) as client:
        with pytest.raises(StorageError) as excinfo:
            await s3kit.probe(client, cfg).run()

    assert "storagekit/s3" in str(excinfo.value)
    assert cfg.bucket in str(excinfo.value)
