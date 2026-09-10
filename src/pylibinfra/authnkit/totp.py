"""TOTP (RFC 6238) поверх pyotp: enrollment, проверка кода и защита от replay."""

from __future__ import annotations

import hashlib
from typing import Self

import pyotp
from pydantic import Field, model_validator

from pylibinfra._internal.configkit import KitSettings
from pylibinfra.clockkit import Clock, Real

from .errors import InvalidSecretError, UsedCodeStoreError, invalid_config
from .metrics import TOTPInstruments
from .usedcode import UsedCodeStore

TOTP_PERIOD = 30
TOTP_DIGITS = 6
MAX_TOTP_SKEW = 2


class TOTPConfig(KitSettings):
    """Issuer в otpauth-URL и окно приёма кода."""

    issuer: str = Field(validation_alias="TOTP_ISSUER", min_length=1)
    skew: int = Field(default=1, validation_alias="TOTP_SKEW", ge=0, le=MAX_TOTP_SKEW)

    @model_validator(mode="after")
    def _check_issuer(self) -> Self:
        if check_label(self.issuer) is not None:
            raise ValueError("TOTP issuer must not contain ':' or '/'")
        return self


def check_label(value: str) -> str | None:
    """Проверяет часть label'а otpauth-URL; текст нарушения либо `None`."""
    if not value or ":" in value or "/" in value:
        return "must be non-empty and free of ':' and '/'"
    return None


class TOTP:
    """Enrollment и проверка кодов TOTP (SHA1, 6 цифр, окно 30 с).

    `verify_once` доступен, только если в конструктор передан `used_codes`;
    без него защиты от повторного ввода кода нет.
    """

    def __init__(
        self,
        cfg: TOTPConfig,
        *,
        clock: Clock | None = None,
        used_codes: UsedCodeStore | None = None,
    ) -> None:
        detail = check_label(cfg.issuer)
        if detail is not None:
            raise invalid_config(f"TOTP issuer {detail}")
        self._issuer = cfg.issuer
        self._skew = cfg.skew
        self._clock = clock if clock is not None else Real()
        self._used_codes = used_codes
        self._instr = TOTPInstruments()

    def generate(self, account: str) -> tuple[str, str]:
        """Новый секрет (base32) и otpauth-URL для QR; `account` с ':' или '/' → `ConfigError`."""
        detail = check_label(account)
        if detail is not None:
            raise invalid_config(f"TOTP account name {detail}")
        secret = pyotp.random_base32()
        url = self._totp(secret).provisioning_uri(name=account, issuer_name=self._issuer)
        return secret, url

    def verify(self, secret: str, code: str) -> bool:
        """Проверяет код в окне ±skew; от повторного ввода не защищает — см. `verify_once`.

        Нечитаемый секрет → `InvalidSecretError` (порча данных, а не «код неверен»).
        """
        ok = self._match(secret, code)
        self._instr.record("ok" if ok else "invalid")
        return ok

    async def verify_once(self, secret: str, code: str) -> bool:
        """`verify` плюс защита от replay: принятый в своём окне код отвергается (`False`).

        Ошибка store → `UsedCodeStoreError`: пускать ли пользователя при
        недоступном хранилище, решает потребитель. Без `used_codes` → `ConfigError`.
        """
        if self._used_codes is None:
            raise invalid_config("TOTP used-code store is required for verify_once")
        if not self._match(secret, code):
            self._instr.record("invalid")
            return False
        try:
            fresh = await self._used_codes.mark_used(_used_code_key(secret, code), self.code_ttl)
        except Exception as exc:
            self._instr.record("store_error")
            raise UsedCodeStoreError(str(exc)) from exc
        self._instr.record("ok" if fresh else "replay")
        return fresh

    @property
    def code_ttl(self) -> float:
        """Сколько секунд код остаётся валидным: своё окно плюс ±skew соседних."""
        return (2 * self._skew + 1) * float(TOTP_PERIOD)

    def _match(self, secret: str, code: str) -> bool:
        try:
            return bool(
                self._totp(secret).verify(code, for_time=self._clock.now(), valid_window=self._skew)
            )
        except ValueError as exc:
            # b32decode бросает binascii.Error — подкласс ValueError.
            self._instr.record("secret_invalid")
            raise InvalidSecretError(str(exc)) from exc

    def _totp(self, secret: str) -> pyotp.TOTP:
        # Параметры заданы явно: verify считает по ним же, а не по дефолтам pyotp.
        return pyotp.TOTP(
            secret,
            digits=TOTP_DIGITS,
            digest=hashlib.sha1,
            interval=TOTP_PERIOD,
        )


def _used_code_key(secret: str, code: str) -> str:
    # Секрет в ключ не попадает: хранилище может быть общим (Redis).
    digest = hashlib.sha256(f"{secret}\x00{code}".encode()).hexdigest()
    return f"authnkit:totp:{digest}"
