"""Hasher-порт и Argon2id-реализация со строгим разбором PHC-строки.

Argon2id считается в `asyncio.to_thread`: одно вычисление занимает десятки
миллисекунд CPU и в event-loop'е блокировало бы все соединения процесса.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import secrets
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol, Self, runtime_checkable

from argon2.low_level import ARGON2_VERSION, Type, hash_secret_raw
from pydantic import Field, model_validator

from pylibinfra._internal.configkit import KitSettings

from .errors import HashParamsError, InvalidHashError, invalid_config
from .metrics import HashInstruments

MIN_TIME = 1
MAX_TIME = 64
MIN_MEMORY_KIB = 8 * 1024
MAX_MEMORY_KIB = 1024 * 1024
MAX_COST_KIB_PASSES = 4 * 3 * 64 * 1024
MIN_THREADS = 1
MAX_THREADS = 16
MIN_SALT_LEN = 8
MAX_SALT_LEN = 64
MIN_KEY_LEN = 16
MAX_KEY_LEN = 128

_DIGITS = frozenset("0123456789")
_WHITESPACE = frozenset(" \t\r\n\v\f")

# Значения не важны — важно, что verify_dummy тратит столько же CPU, сколько
# verify на существующем пользователе.
_DUMMY_PASSWORD = b"authnkit-dummy-password"
_DUMMY_SALT = b"authnkit-dummy-salt-do-not-store-it-anywhere-0123456789abcdefghij"


@runtime_checkable
class Hasher(Protocol):
    """Хэш пароля и проверка против сохранённой PHC-строки."""

    async def hash(self, plain: str) -> str:
        """Самоописывающая PHC-строка для `plain`."""
        ...

    async def verify(self, plain: str, encoded: str) -> bool:
        """Совпал ли `plain` с `encoded`; нечитаемая строка → `InvalidHashError`."""
        ...

    def needs_rehash(self, encoded: str) -> bool:
        """Параметры `encoded` отличаются от текущих параметров хэшера."""
        ...

    async def verify_dummy(self) -> None:
        """Холостая проверка для пути «пользователь не найден»."""
        ...


def check_params(
    time: int, memory_kib: int, threads: int, key_len: int, salt_len: int
) -> str | None:
    """Проверяет окно параметров Argon2id; текст нарушения либо `None`."""
    if not MIN_TIME <= time <= MAX_TIME:
        return f"t must be within {MIN_TIME}..{MAX_TIME}"
    if not MIN_THREADS <= threads <= MAX_THREADS:
        return f"p must be within {MIN_THREADS}..{MAX_THREADS}"
    if not MIN_MEMORY_KIB <= memory_kib <= MAX_MEMORY_KIB:
        return f"m must be within {MIN_MEMORY_KIB}..{MAX_MEMORY_KIB} KiB"
    if time * memory_kib > MAX_COST_KIB_PASSES:
        return f"t*m must not exceed {MAX_COST_KIB_PASSES} KiB-passes"
    if not MIN_KEY_LEN <= key_len <= MAX_KEY_LEN:
        return f"hash length must be within {MIN_KEY_LEN}..{MAX_KEY_LEN} bytes"
    if not MIN_SALT_LEN <= salt_len <= MAX_SALT_LEN:
        return f"salt must be within {MIN_SALT_LEN}..{MAX_SALT_LEN} bytes"
    return None


class Argon2idParams(KitSettings):
    """Параметры Argon2id и одновременно окно, в котором принимаются параметры PHC-строки."""

    time: int = Field(default=3, validation_alias="ARGON2_TIME", ge=MIN_TIME, le=MAX_TIME)
    memory_kib: int = Field(
        default=65536,
        validation_alias="ARGON2_MEMORY_KIB",
        ge=MIN_MEMORY_KIB,
        le=MAX_MEMORY_KIB,
    )
    threads: int = Field(
        default=4, validation_alias="ARGON2_THREADS", ge=MIN_THREADS, le=MAX_THREADS
    )
    key_len: int = Field(
        default=32, validation_alias="ARGON2_KEYLEN", ge=MIN_KEY_LEN, le=MAX_KEY_LEN
    )
    salt_len: int = Field(
        default=16, validation_alias="ARGON2_SALTLEN", ge=MIN_SALT_LEN, le=MAX_SALT_LEN
    )

    @model_validator(mode="after")
    def _check_cost(self) -> Self:
        # Каждый параметр по отдельности в окне (t=64 и m=1 ГиБ), а вместе они
        # стоят десяток секунд и гигабайт на один Verify.
        if self.time * self.memory_kib > MAX_COST_KIB_PASSES:
            raise ValueError(f"t*m must not exceed {MAX_COST_KIB_PASSES} KiB-passes")
        return self


@dataclass(frozen=True, slots=True)
class _PHC:
    """Разобранная PHC-строка: параметры, соль и хэш."""

    params: tuple[int, int, int, int, int]
    salt: bytes
    digest: bytes


class Argon2idHasher:
    """`Hasher` поверх argon2-cffi с самоописывающей PHC-строкой.

    Параметры вне окна кита → `ConfigError` на конструкторе, а не на первом хэше.
    """

    def __init__(self, params: Argon2idParams) -> None:
        detail = check_params(
            params.time, params.memory_kib, params.threads, params.key_len, params.salt_len
        )
        if detail is not None:
            raise invalid_config(f"argon2id: {detail}")
        self._params = params
        self._instr = HashInstruments()

    async def hash(self, plain: str) -> str:
        """Считает Argon2id со случайной солью и кодирует результат в PHC-строку."""
        start = perf_counter()
        try:
            p = self._params
            salt = secrets.token_bytes(p.salt_len)
            digest = await asyncio.to_thread(
                hash_secret_raw,
                plain.encode(),
                salt,
                p.time,
                p.memory_kib,
                p.threads,
                p.key_len,
                Type.ID,
                ARGON2_VERSION,
            )
            return (
                f"$argon2id$v={ARGON2_VERSION}$m={p.memory_kib},t={p.time},p={p.threads}"
                f"${_b64_encode(salt)}${_b64_encode(digest)}"
            )
        finally:
            self._instr.hash_duration.record(perf_counter() - start)

    async def verify(self, plain: str, encoded: str) -> bool:
        """Сверяет `plain` с PHC-строкой.

        `False` — пароль не совпал; `InvalidHashError` — строка битая, неканоничная
        или её параметры вне окна кита (`HashParamsError`).
        """
        start = perf_counter()
        try:
            phc = _parse_phc(encoded)
            time, memory_kib, threads, key_len, _ = phc.params
            digest = await asyncio.to_thread(
                hash_secret_raw,
                plain.encode(),
                phc.salt,
                time,
                memory_kib,
                threads,
                key_len,
                Type.ID,
                ARGON2_VERSION,
            )
            return hmac.compare_digest(digest, phc.digest)
        finally:
            self._instr.verify_duration.record(perf_counter() - start)

    def needs_rehash(self, encoded: str) -> bool:
        """Параметры `encoded` отличаются от параметров хэшера (или строка нечитаема)."""
        try:
            phc = _parse_phc(encoded)
        except InvalidHashError:
            return True
        return phc.params != self._own_params()

    async def verify_dummy(self) -> None:
        """Тратит столько же CPU, сколько `verify`.

        Зови на пути «пользователь не найден»: иначе время ответа перечисляет
        учётные записи.
        """
        start = perf_counter()
        try:
            p = self._params
            await asyncio.to_thread(
                hash_secret_raw,
                _DUMMY_PASSWORD,
                _DUMMY_SALT[: p.salt_len],
                p.time,
                p.memory_kib,
                p.threads,
                p.key_len,
                Type.ID,
                ARGON2_VERSION,
            )
        finally:
            self._instr.verify_duration.record(perf_counter() - start)

    def _own_params(self) -> tuple[int, int, int, int, int]:
        p = self._params
        return (p.time, p.memory_kib, p.threads, p.key_len, p.salt_len)


def _b64_encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii").rstrip("=")


def _b64_decode(field: str, name: str) -> bytes:
    # Каноничность: декодер игнорирует мусор в хвостовых битах последнего
    # символа, и у одного хэша было бы несколько представлений — неканоническое
    # осело бы в БД навсегда.
    try:
        raw = base64.b64decode(field + "=" * (-len(field) % 4), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidHashError(f"{name}: {exc}") from None
    if _b64_encode(raw) != field:
        raise InvalidHashError(f"{name}: non-canonical base64")
    return raw


def _parse_field(field: str, prefix: str, bits: int) -> int:
    digits = field.removeprefix(prefix)
    if digits == field:
        raise InvalidHashError(f"expected {prefix!r}")
    # ParseUint-семантика: ни '+', ни ведущих нулей, ни хвостового мусора —
    # иначе у одной строки несколько представлений.
    if not digits or not _DIGITS.issuperset(digits) or (len(digits) > 1 and digits[0] == "0"):
        raise InvalidHashError(f"{digits!r} is not a canonical number")
    value = int(digits)
    if value >= 1 << bits:
        raise InvalidHashError(f"{digits!r} does not fit in {bits} bits")
    return value


def _parse_phc(encoded: str) -> _PHC:
    if not _WHITESPACE.isdisjoint(encoded):
        raise InvalidHashError("whitespace in hash")
    parts = encoded.split("$")
    if len(parts) != 6 or parts[0] != "" or parts[1] != "argon2id":
        raise InvalidHashError("not a $argon2id$ string")

    version = _parse_field(parts[2], "v=", 32)
    if version != ARGON2_VERSION:
        raise InvalidHashError(f"unsupported version {version}")

    fields = parts[3].split(",")
    if len(fields) != 3:
        raise InvalidHashError("params must be m=,t=,p=")
    memory_kib = _parse_field(fields[0], "m=", 32)
    time = _parse_field(fields[1], "t=", 32)
    threads = _parse_field(fields[2], "p=", 8)

    salt = _b64_decode(parts[4], "salt")
    digest = _b64_decode(parts[5], "hash")
    if len(digest) > MAX_KEY_LEN or len(salt) > MAX_SALT_LEN:
        raise InvalidHashError("hash or salt too long")

    # Окно проверяется до вычисления: иначе t/m из строки задавали бы стоимость
    # одной проверки, а строка приходит из хранилища.
    detail = check_params(time, memory_kib, threads, len(digest), len(salt))
    if detail is not None:
        raise HashParamsError(detail)
    return _PHC((time, memory_kib, threads, len(digest), len(salt)), salt, digest)
