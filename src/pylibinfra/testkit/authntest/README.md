# testkit/authntest

Детерминированный `authnkit.Hasher` для тестов консьюмера: Argon2id стоит
десятки миллисекунд на каждый хэш, и доменный тест, который заводит десяток
пользователей, платит за криптографию больше, чем за собственную логику.

```python
from pylibinfra.testkit.authntest import HASH_PREFIX, DeterministicHasher, strip_hash_prefix
```

## DeterministicHasher

Реализует тот же порт, что `Argon2idHasher`: `hash`/`verify`/`verify_dummy` —
корутины, `needs_rehash` — обычный метод.

| Метод | Поведение |
|---|---|
| `await hash(plain)` | `HASH_PREFIX + plain` (`"hash:s3cret"`), без соли и без стоимости |
| `await verify(plain, encoded)` | `encoded == HASH_PREFIX + plain` |
| `needs_rehash(encoded)` | всегда `False`: параметров у детерминированного хэша нет |
| `await verify_dummy()` | no-op: выравнивать нечего, хэш не считается |
| `strip_hash_prefix(encoded)` | снимает префикс — для ассертов на исходном пароле |

```python
from pylibinfra.authnkit import Hasher
from pylibinfra.testkit.authntest import HASH_PREFIX, DeterministicHasher, strip_hash_prefix


async def main() -> None:
    hasher: Hasher = DeterministicHasher()

    encoded = await hasher.hash("s3cret")
    assert encoded == HASH_PREFIX + "s3cret"
    assert await hasher.verify("s3cret", encoded)
    assert strip_hash_prefix(encoded) == "s3cret"
```

## Предохранитель

Хэш предсказуем и не секретен — фейк годится только для тестов. Вне pytest
(в окружении нет `PYTEST_CURRENT_TEST`, который pytest ставит на время
setup/call/teardown теста) любой метод поднимает `RuntimeError`: фейк, попавший
в production-граф из-за ошибки проводки dishka, падает на первом обращении, а не
кладёт пароли в базу открытым текстом.

Подмена в контейнере — провайдером консьюмера, зарегистрированным ПОСЛЕ
`AuthnProvider`:

```python
class FakeAuthProvider(Provider):
    scope = Scope.APP

    @provide
    def hasher(self) -> Hasher:
        return DeterministicHasher()
```

Управляемые часы для JWT и TOTP и остальные приёмы тестирования кита —
[authnkit/docs/testing.md](../../authnkit/docs/testing.md).
