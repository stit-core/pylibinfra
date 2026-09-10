# authnkit

Криптографически-корректные кирпичики для аутентификации: `Hasher` поверх
Argon2id, JWT sign/verify на HS256 с типом токена (`HS256JWT`, pyjwt),
TOTP по RFC 6238 (`TOTP`, pyotp) и in-process RBAC-реестр (`Registry`). Кит даёт
только примитивы — конкретные auth-flow'ы (login, refresh, выдача и ротация
токенов, MFA enrollment, хранение секретов) живут на стороне потребителя.

Observability-by-default: кит эмитит исход верификаций JWT/TOTP и время
хэширования/проверки пароля. Argon2id считается в `asyncio.to_thread` — одно
вычисление занимает десятки миллисекунд CPU и в event-loop'е блокировало бы все
соединения процесса, поэтому `hash`/`verify`/`verify_dummy` — корутины.

## Импорт

```python
from pylibinfra.authnkit import Argon2idHasher, HS256JWT, TOTP, Registry
```

Версия и установка — см. [корневой README § Использование в проекте](../../../README.md).

## Быстрый старт

```python
import asyncio

from pylibinfra.authnkit import (
    HS256JWT,
    Argon2idHasher,
    Argon2idParams,
    JWTConfig,
    Registry,
    TokenExpiredError,
    WrongTokenTypeError,
)


async def main() -> None:
    # 1. Пароль: хэш → проверка → перехэш при смене параметров.
    hasher = Argon2idHasher(Argon2idParams())  # ConfigError: параметры вне окна
    encoded = await hasher.hash("s3cr3t")  # $argon2id$v=19$m=...$salt$hash
    if await hasher.verify("s3cr3t", encoded) and hasher.needs_rehash(encoded):
        encoded = await hasher.hash("s3cr3t")

    # 2. JWT: секрет ≥ 32 байт; тип токена проверяется при разборе.
    jwt = HS256JWT(JWTConfig(secret="change-me-to-a-32-byte-or-longer-secret"))
    token = jwt.sign_access({"sub": "user-123", "role": "admin"})
    try:
        claims = jwt.parse_access(token)
        print("sub:", claims["sub"])
    except TokenExpiredError:
        pass  # токен протух — отправить на refresh
    except WrongTokenTypeError:
        pass  # в access-контуре предъявили refresh-токен

    # 3. RBAC: реестр ролей (старт приложения) → проверка права.
    roles = Registry()
    roles.register("admin", "articles:read", "articles:write")
    assert roles.has("admin", "articles:write")


if __name__ == "__main__":
    asyncio.run(main())
```

Через dishka — конфиги регистрирует консьюмер, `ClockProvider` обязателен,
второй фактор подключается отдельным `TOTPProvider`:

```python
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.authnkit import (
    Argon2idParams,
    AuthnProvider,
    Hasher,
    JWTConfig,
    JWTSigner,
    Registry,
    new_argon2id_params,
    new_jwt_config,
)
from pylibinfra.clockkit import ClockProvider


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def argon2(self) -> Argon2idParams:
        return new_argon2id_params()  # ARGON2_* из ENV

    @provide
    def jwt(self) -> JWTConfig:
        return new_jwt_config()  # JWT_* из ENV


async def wire() -> None:
    container = make_async_container(AuthnProvider(), ClockProvider(), AppProvider())

    hasher = await container.get(Hasher)
    signer = await container.get(JWTSigner)
    roles = await container.get(Registry)
    roles.register("admin", "users:read", "users:write")

    print(signer.sign_access({"sub": "u1", "role": "admin"}))
    print(await hasher.hash("s3cr3t"))
    await container.close()
```

Сигнатуры API — в [docs/usage.md](docs/usage.md).

## Документация

- [docs/architecture.md](docs/architecture.md) — устройство четырёх примитивов (hashing / JWT / TOTP / RBAC), границы кита, проектные решения (WHY).
- [docs/configuration.md](docs/configuration.md) — полный справочник ENV: `Argon2idParams`, `JWTConfig`, `TOTPConfig`, окна параметров и их валидация.
- [docs/observability.md](docs/observability.md) — метрики и labels (единый источник правды).
- [docs/usage.md](docs/usage.md) — рецепты: dishka и прямой конструктор, хэш пароля, sign/verify JWT, TOTP с защитой от replay, проверка прав.
- [docs/testing.md](docs/testing.md) — `testkit/authntest`: `DeterministicHasher`; управляемые часы для JWT/TOTP.
