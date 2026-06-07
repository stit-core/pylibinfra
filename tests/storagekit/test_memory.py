import pytest

from pylibinfra.storagekit import ErrNotFound, MemoryStorage, Storage, URLBuilder


def new_mem() -> MemoryStorage:
    return MemoryStorage(URLBuilder("https://cdn.example.com", "media"))


def test_memory_storage_satisfies_protocol() -> None:
    assert isinstance(new_mem(), Storage)


async def test_put_returns_stable_ref_and_roundtrip() -> None:
    m = new_mem()
    body = b"hello-bytes"

    ref = await m.put(body, "image/png")

    assert ref.content_type == "image/png"
    assert ref.size == len(body)
    assert ref.url == f"https://cdn.example.com/media/{ref.key}"
    assert ref.key.endswith(".png")
    assert m.get(ref.key) == body


async def test_delete_removes_object() -> None:
    m = new_mem()
    ref = await m.put(b"x", "application/pdf")

    await m.delete(ref.key)
    assert m.get(ref.key) is None


async def test_delete_missing_raises_not_found() -> None:
    m = new_mem()
    with pytest.raises(ErrNotFound):
        await m.delete("does-not-exist")
