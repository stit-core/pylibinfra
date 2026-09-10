from __future__ import annotations

import pytest
from dishka import Provider, Scope, make_async_container, provide
from dishka.exceptions import NoFactoryError

from pylibinfra.authnkit import (
    HS256JWT,
    TOTP,
    Argon2idHasher,
    Argon2idParams,
    AuthnProvider,
    ConfigError,
    Hasher,
    JWTConfig,
    JWTParser,
    JWTSigner,
    MemoryUsedCodeStore,
    OptionalUsedCodeStore,
    Registry,
    TOTPConfig,
    TOTPProvider,
)
from pylibinfra.clockkit import ClockProvider

SECRET = "test-secret-key-of-at-least-32-bytes!!"


class _Configs(Provider):
    scope = Scope.APP

    def __init__(self, *, params: Argon2idParams | None = None) -> None:
        super().__init__()
        self._params = params or Argon2idParams(
            time=1, memory_kib=8192, threads=1, key_len=16, salt_len=8
        )

    @provide
    def params(self) -> Argon2idParams:
        return self._params

    @provide
    def jwt_cfg(self) -> JWTConfig:
        return JWTConfig(secret=SECRET)

    @provide
    def totp_cfg(self) -> TOTPConfig:
        return TOTPConfig(issuer="svc", skew=1)


class _UsedCodes(Provider):
    scope = Scope.APP

    @provide
    def store(self) -> OptionalUsedCodeStore:
        return MemoryUsedCodeStore()


async def test_provider_wiring() -> None:
    container = make_async_container(AuthnProvider(), TOTPProvider(), ClockProvider(), _Configs())

    hasher = await container.get(Hasher)
    assert await container.get(Argon2idHasher) is hasher

    signer = await container.get(JWTSigner)
    parser = await container.get(JWTParser)
    concrete = await container.get(HS256JWT)
    assert signer is parser is concrete

    totp = await container.get(TOTP)
    registry = await container.get(Registry)

    encoded = await hasher.hash("pw")
    assert await hasher.verify("pw", encoded) is True
    assert hasher.needs_rehash(encoded) is False
    await hasher.verify_dummy()

    assert parser.parse_access(signer.sign_access({"sub": "u1"}))["sub"] == "u1"

    secret, _ = totp.generate("u")
    assert totp.verify(secret, "000000") is False

    registry.register("admin", "users:write")
    assert registry.has("admin", "users:write") is True

    await container.close()


async def test_totp_has_no_replay_protection_by_default() -> None:
    container = make_async_container(AuthnProvider(), TOTPProvider(), ClockProvider(), _Configs())
    totp = await container.get(TOTP)
    secret, _ = totp.generate("u")

    with pytest.raises(ConfigError):
        await totp.verify_once(secret, "000000")

    await container.close()


async def test_used_code_store_override() -> None:
    # Провайдер консьюмера регистрируется ПОСЛЕ TOTPProvider и перекрывает дефолт.
    container = make_async_container(
        AuthnProvider(), TOTPProvider(), ClockProvider(), _Configs(), _UsedCodes()
    )
    totp = await container.get(TOTP)
    secret, _ = totp.generate("u")

    assert await totp.verify_once(secret, "000000") is False

    await container.close()


async def test_invalid_config_fails_the_graph() -> None:
    broken = Argon2idParams.model_construct(
        time=0, memory_kib=8192, threads=1, key_len=16, salt_len=8
    )
    container = make_async_container(AuthnProvider(), ClockProvider(), _Configs(params=broken))

    with pytest.raises(ConfigError):
        await container.get(Hasher)

    await container.close()


async def test_authn_provider_does_not_require_totp_config() -> None:
    # Приложение без 2FA не обязано задавать TOTP_ISSUER: TOTP — отдельный provider.
    class _WithoutTOTP(Provider):
        scope = Scope.APP

        @provide
        def params(self) -> Argon2idParams:
            return Argon2idParams(time=1, memory_kib=8192, threads=1, key_len=16, salt_len=8)

        @provide
        def jwt_cfg(self) -> JWTConfig:
            return JWTConfig(secret=SECRET)

    container = make_async_container(AuthnProvider(), ClockProvider(), _WithoutTOTP())

    assert await container.get(Hasher) is not None
    with pytest.raises(NoFactoryError):
        await container.get(TOTP)

    await container.close()
