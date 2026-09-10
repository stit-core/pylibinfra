"""authntest — детерминированный `authnkit.Hasher` для тестов консьюмера.

Argon2 стоит десятки миллисекунд на хэш; `DeterministicHasher` подставляется
вместо него и вне pytest падает с `RuntimeError`.
"""

from .hasher import HASH_PREFIX, DeterministicHasher, strip_hash_prefix

__all__ = ["HASH_PREFIX", "DeterministicHasher", "strip_hash_prefix"]
