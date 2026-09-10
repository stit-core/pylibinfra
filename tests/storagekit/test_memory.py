import io
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest

from pylibinfra.storagekit import (
    URL_STYLE_PATH,
    MemoryStorage,
    Presigner,
    Storage,
    UploadFailedError,
    URLBuilder,
)
from pylibinfra.testkit import storagetest


def new_memory() -> MemoryStorage:
    return MemoryStorage(URLBuilder("https://cdn.example.com", "media", URL_STYLE_PATH))


@pytest.mark.parametrize("case", list(storagetest.CASES))
async def test_contract(case: str) -> None:
    memory = new_memory()
    await storagetest.run_case(case, storagetest.Subject(memory, memory, keys=_keys_of(memory)))


def _keys_of(memory: MemoryStorage) -> Callable[[], Awaitable[list[str]]]:
    async def keys() -> list[str]:
        return memory.keys()

    return keys


def test_satisfies_ports() -> None:
    memory = new_memory()

    assert isinstance(memory, Storage)
    assert isinstance(memory, Presigner)


async def test_put_returns_stable_ref() -> None:
    memory = new_memory()
    body = b"hello-bytes"

    ref = await memory.put(body, "image/png", len(body))

    assert ref.url == f"https://cdn.example.com/media/{ref.key}"
    assert ref.key.endswith(".png")
    assert memory.bytes(ref.key) == body


async def test_bytes_and_keys_helpers() -> None:
    memory = new_memory()
    assert memory.keys() == []

    ref = await memory.put(b"a", "text/plain", 1)

    assert memory.keys() == [ref.key]
    assert memory.bytes("missing") is None

    await memory.delete(ref.key)
    assert memory.keys() == []


async def test_without_url_builder_ref_url_is_key() -> None:
    memory = MemoryStorage()

    ref = await memory.put(b"body", "text/plain", 4)

    assert ref.url == ref.key
    assert await memory.stat(ref.key)


async def test_presign_is_deterministic() -> None:
    memory = new_memory()

    link = await memory.presign_get("obj.ogg", 30)

    assert link == "https://cdn.example.com/media/obj.ogg?x-presigned-method=GET&x-presigned-ttl=30"
    assert await memory.presign_get("obj.ogg", 30) == link


async def test_presign_put_carries_method() -> None:
    memory = new_memory()

    assert "x-presigned-method=PUT" in await memory.presign_put("obj.ogg", 30)


async def test_presign_get_carries_response_headers() -> None:
    memory = new_memory()

    link = await memory.presign_get(
        "obj.pdf",
        60,
        response_content_disposition='attachment; filename="report.pdf"',
        response_content_type="application/pdf",
    )

    assert "response-content-disposition=attachment" in link
    assert "response-content-type=application%2Fpdf" in link


async def test_put_propagates_source_error() -> None:
    memory = new_memory()
    boom = RuntimeError("source is gone")

    async def failing() -> "object":
        yield b"partial"
        raise boom

    with pytest.raises(UploadFailedError) as excinfo:
        await memory.put(failing(), "text/plain", -1)  # type: ignore[arg-type]

    assert excinfo.value.__cause__ is boom
    assert memory.keys() == []


async def test_put_accepts_file_like_source() -> None:
    memory = new_memory()
    body = b"file-like-body"

    ref = await memory.put(io.BytesIO(body), "application/octet-stream", len(body))

    assert memory.bytes(ref.key) == body


async def test_get_returns_reader_over_stored_bytes() -> None:
    memory = new_memory()
    ref = await memory.put(b"0123456789", "text/plain", 10)

    reader, info = await memory.get(ref.key)
    async with reader:
        head = await reader.read(4)
        rest = b"".join([chunk async for chunk in reader])

    assert head == b"0123"
    assert rest == b"456789"
    assert info.size == 10
    assert info.content_type == "text/plain"


async def test_put_accepts_async_read_source() -> None:
    memory = new_memory()

    class AsyncFile:
        def __init__(self, data: bytes) -> None:
            self._buf = bytearray(data)

        async def read(self, n: int = -1) -> bytes:
            take = len(self._buf) if n < 0 else n
            chunk = bytes(self._buf[:take])
            del self._buf[:take]
            return chunk

    ref = await memory.put(AsyncFile(b"async-body"), "text/plain", 10)

    assert memory.bytes(ref.key) == b"async-body"


async def test_put_skips_empty_chunks() -> None:
    memory = new_memory()

    async def gappy() -> AsyncIterator[bytes]:
        yield b"a"
        yield b""
        yield b"b"

    ref = await memory.put(gappy(), "text/plain", 2)

    assert memory.bytes(ref.key) == b"ab"


async def test_put_accepts_bytearray_and_memoryview() -> None:
    memory = new_memory()

    first = await memory.put(bytearray(b"abc"), "text/plain", 3)
    second = await memory.put(memoryview(b"abcd"), "text/plain", 4)

    assert memory.bytes(first.key) == b"abc"
    assert memory.bytes(second.key) == b"abcd"
