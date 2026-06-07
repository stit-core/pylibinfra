"""Unit-тесты authntest.DeterministicHasher (без Docker)."""

from __future__ import annotations

from pylibinfra.authnkit import Hasher
from pylibinfra.testkit.authntest import HASH_PREFIX, DeterministicHasher, strip_hash_prefix


def test_hash_verify_round_trip() -> None:
    h = DeterministicHasher()
    # Удовлетворяет порту authnkit.Hasher.
    _: Hasher = h

    encoded = h.hash("s3cret")
    assert encoded == HASH_PREFIX + "s3cret"
    assert h.verify("s3cret", encoded) is True
    assert h.verify("wrong", encoded) is False


def test_strip_hash_prefix() -> None:
    assert strip_hash_prefix(HASH_PREFIX + "plain") == "plain"
    assert strip_hash_prefix("plain") == "plain"
