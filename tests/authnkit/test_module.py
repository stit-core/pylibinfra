from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.authnkit import (
    Argon2idParams,
    AuthnProvider,
    Hasher,
    JWTConfig,
    JWTParser,
    JWTSigner,
)


class _Configs(Provider):
    scope = Scope.APP

    @provide
    def params(self) -> Argon2idParams:
        return Argon2idParams(ARGON2_TIME=1, ARGON2_MEMORY_KIB=8, ARGON2_THREADS=1)

    @provide
    def jwt_cfg(self) -> JWTConfig:
        return JWTConfig(JWT_SECRET="di-secret")


async def test_authn_provider_wiring() -> None:
    container = make_async_container(AuthnProvider(), _Configs())

    hasher = await container.get(Hasher)
    encoded = hasher.hash("pw")
    assert hasher.verify("pw", encoded) is True

    signer = await container.get(JWTSigner)
    parser = await container.get(JWTParser)
    # Один инстанс под оба Protocol (AnyOf).
    assert signer is parser

    tok = signer.sign({"sub": "u1"}, 60)
    assert parser.parse(tok)["sub"] == "u1"

    await container.close()
