"""authntest — быстрый детерминированный authnkit.Hasher для тестов консьюмера
(без Argon2id-стоимости)."""

from .hasher import HASH_PREFIX, DeterministicHasher, strip_hash_prefix

__all__ = ["HASH_PREFIX", "DeterministicHasher", "strip_hash_prefix"]
