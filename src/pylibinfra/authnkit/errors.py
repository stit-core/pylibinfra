"""Ошибки authnkit: конфиг, PHC-строка, разбор токена, TOTP.

Иерархия повторяет контракт кита: `HashParamsError` — частный случай
`InvalidHashError`, все исходы разбора токена — `TokenError`, ошибки второго
фактора — `TOTPError`.
"""

from __future__ import annotations

from typing import ClassVar

from pylibinfra._internal import configkit

KIT = "authnkit"


class ConfigError(configkit.ConfigError):
    """Конфиг кита вне допустимого окна значений."""


def invalid_config(detail: str) -> ConfigError:
    """Собирает `ConfigError` с текстом `authnkit: invalid config: <detail>`."""
    return ConfigError(f"{KIT}: invalid config: {detail}")


class InvalidHashError(Exception):
    """PHC-строка нечитаема, неканонична или её параметры вне окна кита."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"{KIT}: invalid argon2id hash: {detail}")


class HashParamsError(InvalidHashError):
    """Параметры PHC-строки вне окна кита — частный случай `InvalidHashError`."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"parameters out of window: {detail}")


class TokenError(Exception):
    """База ошибок разбора токена.

    `result` — значение label'а `result` метрики `authn_jwt_verifications_total`,
    которое `HS256JWT.parse*` пишет перед тем, как поднять исключение.
    """

    result: ClassVar[str] = "malformed"
    message: ClassVar[str] = f"{KIT}: token error"

    def __init__(self, detail: str = "") -> None:
        super().__init__(f"{self.message}: {detail}" if detail else self.message)


class TokenExpiredError(TokenError):
    """Истёк `exp` (с учётом leeway)."""

    result: ClassVar[str] = "expired"
    message: ClassVar[str] = f"{KIT}: token expired"


class TokenMalformedError(TokenError):
    """Токен нечитаем, alg не HS256, нет `exp` или `nbf`/`iat` в будущем."""

    result: ClassVar[str] = "malformed"
    message: ClassVar[str] = f"{KIT}: token malformed"


class TokenSignatureError(TokenError):
    """Подпись не сошлась."""

    result: ClassVar[str] = "signature_invalid"
    message: ClassVar[str] = f"{KIT}: token signature invalid"


class WrongTokenTypeError(TokenError):
    """Тип токена не тот, который ждёт `parse_access`/`parse_refresh`."""

    result: ClassVar[str] = "wrong_type"
    message: ClassVar[str] = f"{KIT}: wrong token type"


class TokenAudienceError(TokenError):
    """`iss` или `aud` токена не совпали с ожидаемыми."""

    result: ClassVar[str] = "audience_mismatch"
    message: ClassVar[str] = f"{KIT}: token issuer or audience mismatch"


class TOTPError(Exception):
    """База ошибок второго фактора."""


class InvalidSecretError(TOTPError):
    """Секрет не читается как base32 — порча данных в хранилище, а не ошибка ввода."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"{KIT}: totp: invalid secret: {detail}")


class UsedCodeStoreError(TOTPError):
    """`UsedCodeStore` недоступен: пускать ли пользователя, решает потребитель."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"{KIT}: totp: used-code store: {detail}")
