"""Контрактный набор storagetest: прогон по in-memory адаптеру и диагностика отказов."""

from typing import Any

import pytest

from pylibinfra.storagekit import URL_STYLE_PATH, MemoryStorage, Ref, URLBuilder
from pylibinfra.testkit import storagetest


def new_memory() -> MemoryStorage:
    return MemoryStorage(URLBuilder("https://cdn.example.com", "media", URL_STYLE_PATH))


async def test_contract_passes_on_memory_storage_without_keys_hook() -> None:
    def new_subject() -> storagetest.Subject:
        memory = new_memory()
        return storagetest.Subject(memory, memory)

    await storagetest.run_contract(new_subject)


async def test_contract_accepts_an_async_factory() -> None:
    async def new_subject() -> storagetest.Subject:
        memory = new_memory()

        async def keys() -> list[str]:
            return memory.keys()

        return storagetest.Subject(memory, memory, keys=keys)

    await storagetest.run_contract(new_subject)


def test_cases_are_named() -> None:
    assert "put_get_stat_delete" in storagetest.CASES
    assert all(callable(case) for case in storagetest.CASES.values())


async def test_unknown_case_is_reported() -> None:
    memory = new_memory()

    with pytest.raises(KeyError):
        await storagetest.run_case("nope", storagetest.Subject(memory, memory))


async def test_failure_names_the_case() -> None:
    class Sloppy(MemoryStorage):
        async def put(self, body: Any, content_type: str, size: int) -> Ref:
            return await super().put(body, content_type, -1)

    sloppy = Sloppy()

    with pytest.raises(AssertionError) as excinfo:
        await storagetest.run_case("put_rejects_size_mismatch", storagetest.Subject(sloppy, sloppy))

    assert "storagetest: put_rejects_size_mismatch" in str(excinfo.value)


async def test_wrong_error_type_is_reported() -> None:
    class Rude(MemoryStorage):
        async def delete(self, key: str) -> None:
            if not key:
                raise ValueError("storage says no")
            await super().delete(key)

    rude = Rude()

    with pytest.raises(AssertionError) as excinfo:
        await storagetest.run_case("empty_key_rejected", storagetest.Subject(rude, rude))

    assert "raised ValueError" in str(excinfo.value)
