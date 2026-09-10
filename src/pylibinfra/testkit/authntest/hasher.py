"""DeterministicHasher — `authnkit.Hasher` без Argon2 и предохранитель к нему."""

from __future__ import annotations

import os

HASH_PREFIX = "hash:"


class DeterministicHasher:
    """`authnkit.Hasher` без криптографии: хэш = `HASH_PREFIX` + пароль.

    Вне pytest любой вызов поднимает `RuntimeError`: фейк, попавший в
    production-граф по ошибке проводки, падает на первом обращении, а не кладёт
    пароли в базу открытым текстом.
    """

    async def hash(self, plain: str) -> str:
        """Возвращает `HASH_PREFIX` + `plain`."""
        _must_run_in_test()
        return HASH_PREFIX + plain

    async def verify(self, plain: str, encoded: str) -> bool:
        """Сравнивает `encoded` с `HASH_PREFIX` + `plain`."""
        _must_run_in_test()
        return encoded == HASH_PREFIX + plain

    def needs_rehash(self, encoded: str) -> bool:
        """Всегда `False`: параметров у детерминированного хэша нет."""
        _must_run_in_test()
        return False

    async def verify_dummy(self) -> None:
        """No-op: выравнивать нечего, хэш не считается."""
        _must_run_in_test()


def strip_hash_prefix(encoded: str) -> str:
    """Снимает `HASH_PREFIX` — для ассертов на исходном пароле."""
    return encoded.removeprefix(HASH_PREFIX)


def _must_run_in_test() -> None:
    # PYTEST_CURRENT_TEST ставит сам pytest на время setup/call/teardown теста —
    # аналог testing.Testing() в Go.
    if "PYTEST_CURRENT_TEST" not in os.environ:
        raise RuntimeError("testkit/authntest: DeterministicHasher used outside pytest")
