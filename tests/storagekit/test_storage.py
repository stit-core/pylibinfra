import mimetypes
from collections.abc import AsyncIterator
from uuid import UUID

import pytest

from pylibinfra.storagekit import (
    DEFAULT_CONTENT_TYPE,
    MAX_PRESIGN_TTL,
    MIN_PRESIGN_TTL,
    ObjectReader,
    PresignFailedError,
    new_key,
    normalize_content_type,
    validate_presign_ttl,
)

EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
    "image/avif": ".avif",
    "application/pdf": ".pdf",
    "application/json": ".json",
    "application/zip": ".zip",
    "application/xml": ".xml",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/webm": ".weba",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "text/html": ".html",
    "text/plain; charset=utf-8": ".txt",
    "TEXT/PLAIN": ".txt",
    "application/octet-stream": "",
    "application/x-unknown-type-42": "",
    "": "",
}


@pytest.mark.parametrize(("content_type", "want_ext"), EXTENSIONS.items())
def test_new_key_ext_by_content_type(content_type: str, want_ext: str) -> None:
    key = new_key(content_type)

    assert key.endswith(want_ext)
    ident = key.removesuffix(want_ext) if want_ext else key
    assert UUID(ident).version == 7


def test_new_key_ext_from_mime_database() -> None:
    mimetypes.add_type("application/x-stit-fixture", ".stitfix")

    assert new_key("application/x-stit-fixture").endswith(".stitfix")


def test_new_key_is_unique_and_sortable() -> None:
    keys = [new_key("image/png") for _ in range(100)]

    assert len(set(keys)) == 100
    assert keys == sorted(keys)


@pytest.mark.parametrize(
    ("value", "want"),
    [("", DEFAULT_CONTENT_TYPE), ("   ", DEFAULT_CONTENT_TYPE), ("image/png", "image/png")],
)
def test_normalize_content_type(value: str, want: str) -> None:
    assert normalize_content_type(value) == want


@pytest.mark.parametrize("ttl", [MIN_PRESIGN_TTL, 60.0, MAX_PRESIGN_TTL])
def test_validate_presign_ttl_accepts(ttl: float) -> None:
    validate_presign_ttl(ttl)


@pytest.mark.parametrize("ttl", [-1.0, 0.0, 0.999, MAX_PRESIGN_TTL + 1])
def test_validate_presign_ttl_rejects(ttl: float) -> None:
    with pytest.raises(PresignFailedError):
        validate_presign_ttl(ttl)


async def test_object_reader_reads_whole_body() -> None:
    reader = ObjectReader.from_bytes(b"payload")

    assert await reader.read() == b"payload"
    assert await reader.read() == b""


async def test_object_reader_reads_by_chunks() -> None:
    reader = ObjectReader.from_bytes(b"0123456789")

    assert await reader.read(4) == b"0123"
    assert await reader.read(4) == b"4567"
    assert await reader.read(4) == b"89"
    assert await reader.read(4) == b""


async def test_object_reader_iterates_remaining_body() -> None:
    reader = ObjectReader.from_bytes(b"0123456789")
    assert await reader.read(4) == b"0123"

    rest = [chunk async for chunk in reader]

    assert b"".join(rest) == b"456789"


async def test_object_reader_closes_once() -> None:
    closed = []

    async def close() -> None:
        closed.append(1)

    async def chunks() -> "object":
        yield b"a"

    reader = ObjectReader(chunks(), close=close)  # type: ignore[arg-type]
    async with reader:
        assert await reader.read() == b"a"

    await reader.aclose()
    assert closed == [1]


async def test_object_reader_empty_body() -> None:
    reader = ObjectReader.from_bytes(b"")

    assert await reader.read() == b""
    assert [chunk async for chunk in reader] == []


async def test_object_reader_finalizes_a_generator_source() -> None:
    finalized = []

    async def chunks() -> AsyncIterator[bytes]:
        try:
            yield b"head"
            yield b"tail"
        finally:
            finalized.append(1)

    reader = ObjectReader(chunks())
    assert await reader.read(2) == b"he"

    await reader.aclose()

    assert finalized == [1]


async def test_object_reader_accepts_a_plain_async_iterator() -> None:
    class Once:
        def __init__(self) -> None:
            self._left = [b"only"]

        def __aiter__(self) -> "Once":
            return self

        async def __anext__(self) -> bytes:
            if not self._left:
                raise StopAsyncIteration
            return self._left.pop()

    reader = ObjectReader(Once())

    assert await reader.read() == b"only"
    await reader.aclose()


async def test_object_reader_iterates_every_chunk() -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b"one"
        yield b"two"
        yield b"three"

    reader = ObjectReader(chunks())

    assert [chunk async for chunk in reader] == [b"one", b"two", b"three"]
    assert await reader.read() == b""
