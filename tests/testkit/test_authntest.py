"""Unit-тесты authntest.DeterministicHasher (без Docker)."""

from __future__ import annotations

import pytest

from pylibinfra.authnkit import Hasher
from pylibinfra.testkit.authntest import HASH_PREFIX, DeterministicHasher, strip_hash_prefix


async def test_hash_verify_round_trip() -> None:
    h: Hasher = DeterministicHasher()
    assert isinstance(h, Hasher)

    encoded = await h.hash("s3cret")
    assert encoded == HASH_PREFIX + "s3cret"
    assert await h.verify("s3cret", encoded) is True
    assert await h.verify("wrong", encoded) is False


async def test_needs_rehash_and_verify_dummy_are_no_ops() -> None:
    h: Hasher = DeterministicHasher()
    encoded = await h.hash("s3cret")

    assert h.needs_rehash(encoded) is False
    assert h.needs_rehash("garbage") is False
    await h.verify_dummy()

    # verify_dummy не трогает состояние хэшера.
    assert await h.verify("s3cret", encoded) is True


def test_strip_hash_prefix() -> None:
    assert strip_hash_prefix(HASH_PREFIX + "plain") == "plain"
    assert strip_hash_prefix("plain") == "plain"


async def test_guard_outside_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    # Фейк, попавший в production-граф, обязан падать на первом вызове.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    h = DeterministicHasher()

    with pytest.raises(RuntimeError, match="outside pytest"):
        await h.hash("s3cret")
    with pytest.raises(RuntimeError, match="outside pytest"):
        await h.verify("s3cret", "hash:s3cret")
    with pytest.raises(RuntimeError, match="outside pytest"):
        h.needs_rehash("hash:s3cret")
    with pytest.raises(RuntimeError, match="outside pytest"):
        await h.verify_dummy()
