"""JWTSigner/JWTParser-порты и HS256-реализация поверх pyjwt.

Алгоритм закреплён (`HS256`), `exp` обязателен, сроки сверяются с `clockkit.Clock`
и допуском `JWT_LEEWAY`, исходы разбора попадают в `authn_jwt_verifications_total`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

import jwt
from jwt.types import Options
from pydantic import Field, SecretStr, field_validator

from pylibinfra._internal.configkit import KitSettings
from pylibinfra.clockkit import Clock, Real

from .errors import (
    TokenAudienceError,
    TokenError,
    TokenExpiredError,
    TokenMalformedError,
    TokenSignatureError,
    WrongTokenTypeError,
    invalid_config,
)
from .metrics import JWTInstruments

ALGORITHM = "HS256"
MIN_SECRET_LEN = 32
DEFAULT_ACCESS_TTL = 900.0
DEFAULT_REFRESH_TTL = 2_592_000.0
DEFAULT_LEEWAY = 60.0

# Имя claim'а и его значения — часть формата токена, не секрет (S105).
CLAIM_TOKEN_TYPE = "typ"  # noqa: S105
TOKEN_TYPE_ACCESS = "access"  # noqa: S105
TOKEN_TYPE_REFRESH = "refresh"  # noqa: S105

# Сроки и тип токена кит считает сам — pyjwt проверяет только подпись, алгоритм
# и наличие exp, иначе «сейчас» бралось бы из системных часов в обход Clock.
_DECODE_OPTIONS: Options = {
    "require": ["exp"],
    "verify_exp": False,
    "verify_nbf": False,
    "verify_iat": False,
    "verify_aud": False,
    "verify_iss": False,
}


class JWTConfig(KitSettings):
    """Секрет, сроки жизни токенов и допуск рассинхрона часов."""

    secret: SecretStr = Field(validation_alias="JWT_SECRET")
    access_ttl: float = Field(default=DEFAULT_ACCESS_TTL, validation_alias="JWT_ACCESS_TTL", gt=0)
    refresh_ttl: float = Field(
        default=DEFAULT_REFRESH_TTL, validation_alias="JWT_REFRESH_TTL", gt=0
    )
    leeway: float = Field(default=DEFAULT_LEEWAY, validation_alias="JWT_LEEWAY", ge=0)

    @field_validator("secret")
    @classmethod
    def _check_secret(cls, secret: SecretStr) -> SecretStr:
        # Проверка полевая, а не модельная: иначе ошибка соседнего поля
        # печатала бы в input_value весь конфиг вместе с секретом.
        detail = check_secret(secret.get_secret_value())
        if detail is not None:
            raise ValueError(detail)
        return secret


def check_secret(secret: str) -> str | None:
    """Проверяет длину HMAC-секрета; текст нарушения либо `None`."""
    if len(secret.encode()) < MIN_SECRET_LEN:
        return f"JWT secret must be at least {MIN_SECRET_LEN} bytes"
    return None


@runtime_checkable
class JWTSigner(Protocol):
    """Выпуск токенов: с явным TTL и типизированные access/refresh."""

    def sign(self, claims: dict[str, Any], ttl: float) -> str:
        """Токен с явным `ttl` (секунды) и без claim'а типа."""
        ...

    def sign_access(self, claims: dict[str, Any]) -> str:
        """Токен с `JWTConfig.access_ttl` и `typ=access`."""
        ...

    def sign_refresh(self, claims: dict[str, Any]) -> str:
        """Токен с `JWTConfig.refresh_ttl` и `typ=refresh`."""
        ...


@runtime_checkable
class JWTParser(Protocol):
    """Разбор токенов: без проверки типа и с проверкой access/refresh."""

    def parse(self, token: str) -> dict[str, Any]:
        """Подпись и сроки; тип токена не проверяется."""
        ...

    def parse_access(self, token: str) -> dict[str, Any]:
        """`parse` плюс `typ=access`, иначе `WrongTokenTypeError`."""
        ...

    def parse_refresh(self, token: str) -> dict[str, Any]:
        """`parse` плюс `typ=refresh`, иначе `WrongTokenTypeError`."""
        ...


class HS256JWT:
    """`JWTSigner` и `JWTParser` на HS256 поверх pyjwt.

    `issuer`/`audience` симметричны: заданное значение проставляется при подписи
    и требуется при разборе (`TokenAudienceError`), не заданное — не проверяется.
    """

    def __init__(
        self,
        cfg: JWTConfig,
        *,
        clock: Clock | None = None,
        issuer: str | None = None,
        audience: str | Sequence[str] | None = None,
    ) -> None:
        detail = check_secret(cfg.secret.get_secret_value())
        if detail is not None:
            raise invalid_config(detail)
        self._secret = cfg.secret.get_secret_value()
        self._access_ttl = cfg.access_ttl
        self._refresh_ttl = cfg.refresh_ttl
        self._leeway = cfg.leeway
        self._issuer = issuer
        self._audience = _audience_tuple(audience)
        self._clock = clock if clock is not None else Real()
        self._instr = JWTInstruments()

    def sign(self, claims: dict[str, Any], ttl: float) -> str:
        """Копирует `claims` и проставляет `iat`/`exp` плюс заданные `iss`/`aud`.

        `ttl` — секунды; `ttl <= 0` → `ConfigError`.
        """
        if ttl <= 0:
            raise invalid_config("token ttl must be > 0")
        now = self._clock.now().timestamp()
        payload = dict(claims)
        payload["iat"] = int(now)
        payload["exp"] = int(now + ttl)
        if self._issuer:
            payload["iss"] = self._issuer
        if len(self._audience) == 1:
            payload["aud"] = self._audience[0]
        elif self._audience:
            payload["aud"] = list(self._audience)
        return jwt.encode(payload, self._secret, algorithm=ALGORITHM)

    def sign_access(self, claims: dict[str, Any]) -> str:
        """Токен с `JWTConfig.access_ttl` и `typ=access` (значение caller'а перетирается)."""
        return self.sign({**claims, CLAIM_TOKEN_TYPE: TOKEN_TYPE_ACCESS}, self._access_ttl)

    def sign_refresh(self, claims: dict[str, Any]) -> str:
        """Токен с `JWTConfig.refresh_ttl` и `typ=refresh` (значение caller'а перетирается)."""
        return self.sign({**claims, CLAIM_TOKEN_TYPE: TOKEN_TYPE_REFRESH}, self._refresh_ttl)

    def parse(self, token: str) -> dict[str, Any]:
        """Проверяет подпись, обязательный `exp`, сроки с leeway и заданные `iss`/`aud`."""
        return self._parse(token, None)

    def parse_access(self, token: str) -> dict[str, Any]:
        """`parse` плюс `typ=access`; иначе `WrongTokenTypeError`."""
        return self._parse(token, TOKEN_TYPE_ACCESS)

    def parse_refresh(self, token: str) -> dict[str, Any]:
        """`parse` плюс `typ=refresh`; иначе `WrongTokenTypeError`."""
        return self._parse(token, TOKEN_TYPE_REFRESH)

    def _parse(self, token: str, want_type: str | None) -> dict[str, Any]:
        try:
            claims = self._decode(token)
            self._check_scope(claims)
            self._check_time(claims)
            if want_type is not None and claims.get(CLAIM_TOKEN_TYPE) != want_type:
                raise WrongTokenTypeError(f"expected {want_type}")
        except TokenError as exc:
            self._instr.record(exc.result)
            raise
        self._instr.record("ok")
        return claims

    def _decode(self, token: str) -> dict[str, Any]:
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                self._secret,
                algorithms=[ALGORITHM],
                options=_DECODE_OPTIONS,
            )
        except jwt.InvalidSignatureError:
            raise TokenSignatureError from None
        except jwt.InvalidTokenError as exc:
            raise TokenMalformedError(type(exc).__name__) from None
        return claims

    def _check_scope(self, claims: dict[str, Any]) -> None:
        if self._issuer and claims.get("iss") != self._issuer:
            raise TokenAudienceError("issuer mismatch")
        if not self._audience:
            return
        if _audience_set(claims.get("aud")).isdisjoint(self._audience):
            raise TokenAudienceError("audience mismatch")

    def _check_time(self, claims: dict[str, Any]) -> None:
        now = self._clock.now().timestamp()
        if now - self._leeway > _numeric(claims, "exp"):
            raise TokenExpiredError
        nbf = claims.get("nbf")
        if nbf is not None and now + self._leeway < _numeric(claims, "nbf"):
            raise TokenMalformedError("nbf is in the future")
        iat = claims.get("iat")
        if iat is not None and now + self._leeway < _numeric(claims, "iat"):
            raise TokenMalformedError("iat is in the future")


def _audience_tuple(audience: str | Sequence[str] | None) -> tuple[str, ...]:
    if audience is None:
        return ()
    return (audience,) if isinstance(audience, str) else tuple(audience)


def _audience_set(aud: Any) -> set[str]:
    # Нестроковые элементы отбрасываются: `aud` приходит из чужого токена и
    # может содержать что угодно, вплоть до нехэшируемого.
    if isinstance(aud, str):
        return {aud}
    if isinstance(aud, list):
        return {item for item in aud if isinstance(item, str)}
    return set()


def _numeric(claims: dict[str, Any], name: str) -> float:
    value = claims.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TokenMalformedError(f"{name} must be a number")
    return float(value)
