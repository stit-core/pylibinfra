"""Детерминированный быстрый Hasher для тестов: без Argon2id-стоимости, чтобы
тесты консьюмера были быстрыми."""

HASH_PREFIX = "hash:"


class DeterministicHasher:
    """Фейковый authnkit.Hasher: hash = HASH_PREFIX + plain, verify сравнивает
    строкой. Не криптографичен — только для тестов."""

    def hash(self, plain: str) -> str:
        return HASH_PREFIX + plain

    def verify(self, plain: str, encoded: str) -> bool:
        return encoded == HASH_PREFIX + plain


def strip_hash_prefix(encoded: str) -> str:
    return encoded.removeprefix(HASH_PREFIX)
