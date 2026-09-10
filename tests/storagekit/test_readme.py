"""Сниппеты README storagekit — исполняемые; таблица ENV сверяется с Config."""

import re
from pathlib import Path

import pytest
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import healthkit, logkit, storagekit
from pylibinfra.storagekit import s3
from pylibinfra.testkit import storagetest

README = Path(storagekit.__file__).with_name("README.md")


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def storage_config(self) -> storagekit.Config:
        return storagekit.new_config()

    @provide
    def log(self) -> logkit.Logger:
        return logkit.nop()

    @provide
    def probes(self, client: s3.Client, cfg: storagekit.Config) -> healthkit.Probes:
        return (s3.probe(client, cfg),)


async def test_readme_quickstart(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_S3_ENDPOINT", "127.0.0.1:1")
    monkeypatch.setenv("STORAGE_S3_BUCKET", "media")
    monkeypatch.setenv("STORAGE_S3_ACCESS_KEY", "key")
    monkeypatch.setenv("STORAGE_S3_SECRET_KEY", "secret")
    monkeypatch.setenv("STORAGE_PUBLIC_BASE_URL", "https://cdn.example.com")
    monkeypatch.setenv("STORAGE_S3_PROBE_ON_START", "false")

    container = make_async_container(
        storagekit.StoragekitProvider(), s3.S3Provider(), AppProvider()
    )
    storage = await container.get(storagekit.Storage)
    presigner = await container.get(storagekit.Presigner)
    probes = await container.get(healthkit.Probes)

    assert storage is presigner
    assert [probe.name for probe in probes] == ["storagekit"]
    await container.close()


async def test_readme_port_operations() -> None:
    storage: storagekit.Storage = storagekit.MemoryStorage(
        storagekit.URLBuilder("https://cdn.example.com", "media", storagekit.URL_STYLE_PATH)
    )
    body = b"png-bytes"
    size = len(body)

    ref = await storage.put(body, "image/png", size)

    reader, info = await storage.get(ref.key)
    async with reader:
        data = await reader.read()

    assert data == body
    assert info.content_type == "image/png"
    assert ref.url == f"https://cdn.example.com/media/{ref.key}"
    assert ref.key.endswith(".png")

    await storage.stat(ref.key)
    await storage.delete(ref.key)
    await storage.delete(ref.key)


async def test_readme_error_handling() -> None:
    storage = storagekit.MemoryStorage()

    with pytest.raises(storagekit.SizeMismatchError):
        await storage.put(b"png-bytes", "image/png", 3)
    with pytest.raises(storagekit.UploadFailedError):
        await storage.put(b"png-bytes", "image/png", 3)
    with pytest.raises(storagekit.NotFoundError):
        await storage.stat("missing.png")
    with pytest.raises(storagekit.GetFailedError):
        await storage.get("missing.png")


async def test_readme_presign() -> None:
    presigner: storagekit.Presigner = storagekit.MemoryStorage()
    key = "report.pdf"

    link = await presigner.presign_get(
        key,
        900.0,
        response_content_disposition='attachment; filename="report.pdf"',
        response_content_type="application/pdf",
    )
    upload = await presigner.presign_put(f"inbox/{key}", 900.0)

    assert "response-content-disposition" in link
    assert "inbox/report.pdf" in upload
    with pytest.raises(storagekit.PresignFailedError):
        await presigner.presign_get(key, 0.5)


def test_readme_url_builder_and_new_key() -> None:
    urls = storagekit.URLBuilder("https://cdn.example.com", "media", storagekit.URL_STYLE_PATH)

    assert urls.build("2026/07/abc.png") == "https://cdn.example.com/media/2026/07/abc.png"
    assert storagekit.new_key("image/png").endswith(".png")
    assert "." not in storagekit.new_key("application/octet-stream")


async def test_readme_memory_storage() -> None:
    memory = storagekit.MemoryStorage(
        storagekit.URLBuilder("https://cdn.example.com", "media", storagekit.URL_STYLE_PATH)
    )
    data = b"image-bytes"

    ref = await memory.put(data, "image/png", len(data))

    assert memory.bytes(ref.key) == data
    assert memory.keys() == [ref.key]


async def test_readme_testing_snippets() -> None:
    def new_subject() -> storagetest.Subject:
        memory = storagekit.MemoryStorage()
        return storagetest.Subject(memory, memory)

    await storagetest.run_contract(new_subject)
    await storagetest.run_case("missing_object", new_subject())


async def test_readme_memory_storage_without_urls() -> None:
    without_urls = storagekit.MemoryStorage()

    ref = await without_urls.put(b"x", "text/plain", 1)

    assert ref.url == ref.key


def test_readme_env_table_matches_config() -> None:
    documented = dict(
        re.findall(
            r"^\| `(STORAGE_[A-Z0-9_]+)` \| `([a-z_]+)` \|", README.read_text(), re.MULTILINE
        )
    )
    declared = {
        str(info.validation_alias): name for name, info in storagekit.Config.model_fields.items()
    }

    assert documented == declared


def test_readme_documents_emitted_metrics() -> None:
    text = README.read_text()

    assert "storage_ops_total" in text
    assert "storage_op_duration_seconds" in text
    assert "`storagekit`" in text
