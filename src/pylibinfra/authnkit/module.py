from dishka import AnyOf, Provider, Scope, provide

from .hasher import Argon2idHasher, Argon2idParams, Hasher
from .jwt import HS256JWT, JWTConfig, JWTParser, JWTSigner


# Provider не provide'ит Argon2idParams / JWTConfig (asymmetry note, см.
# pgkit/module.py) — caller регистрирует их сам. Дешёвый mode — new_*-фабрики
# ниже. Один HS256JWT-инстанс отдаётся под оба Protocol через AnyOf.
class AuthnProvider(Provider):
    scope = Scope.APP

    @provide(provides=Hasher)
    def hasher(self, params: Argon2idParams) -> Argon2idHasher:
        return Argon2idHasher(params)

    @provide(provides=AnyOf[JWTSigner, JWTParser])
    def jwt(self, cfg: JWTConfig) -> HS256JWT:
        return HS256JWT(cfg)


def new_argon2id_params() -> Argon2idParams:
    return Argon2idParams()


def new_jwt_config() -> JWTConfig:
    return JWTConfig()
