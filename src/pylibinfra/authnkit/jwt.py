"""JWTSigner/JWTParser-порты и HS256-реализация поверх pyjwt.

TTL — секунды (а не Go-duration строки). Исключения pyjwt маппятся в стабильные
sentinel-ошибки (ErrTokenExpired/ErrTokenSignature/ErrTokenMalformed), чтобы
consumer не зависел от внутренних типов pyjwt.
"""

import time
from typing import Any, Protocol, runtime_checkable

import jwt
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .metrics import record_jwt_result


class JWTConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    secret: str = Field(validation_alias="JWT_SECRET")
    # секунды
    access_ttl: float = Field(default=900.0, validation_alias="JWT_ACCESS_TTL")
    refresh_ttl: float = Field(default=2592000.0, validation_alias="JWT_REFRESH_TTL")


@runtime_checkable
class JWTSigner(Protocol):
    def sign(self, claims: dict[str, Any], ttl: float) -> str: ...


@runtime_checkable
class JWTParser(Protocol):
    def parse(self, token: str) -> dict[str, Any]: ...


class TokenError(Exception):
    """Базовый класс sentinel-ошибок токена."""


# Имена сохранены 1:1 с golibinfra sentinel-ошибками (Err*-нейминг — public API).
class ErrTokenExpired(TokenError):  # noqa: N818
    pass


class ErrTokenMalformed(TokenError):  # noqa: N818
    pass


class ErrTokenSignature(TokenError):  # noqa: N818
    pass


class HS256JWT:
    def __init__(self, cfg: JWTConfig) -> None:
        self._secret = cfg.secret

    def sign(self, claims: dict[str, Any], ttl: float) -> str:
        now = int(time.time())
        payload = dict(claims)
        payload["iat"] = now
        payload["exp"] = now + int(ttl)
        return jwt.encode(payload, self._secret, algorithm="HS256")

    def parse(self, token: str) -> dict[str, Any]:
        try:
            claims: dict[str, Any] = jwt.decode(token, self._secret, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            record_jwt_result("expired")
            raise ErrTokenExpired from None
        except jwt.InvalidSignatureError:
            record_jwt_result("signature_invalid")
            raise ErrTokenSignature from None
        except jwt.InvalidTokenError:
            record_jwt_result("malformed")
            raise ErrTokenMalformed from None
        record_jwt_result("ok")
        return claims
