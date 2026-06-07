"""Hasher-port и Argon2id-реализация поверх argon2-cffi.

argon2-cffi сам генерирует соль и кодирует параметры в PHC-строку
(``$argon2id$v=19$m=...,t=...,p=...$<salt>$<hash>``) — отдельный ручной
encode/decode (как в golibinfra поверх low-level argon2.IDKey) не нужен.
"""

import time
from typing import Protocol, runtime_checkable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .metrics import record_hash_duration, record_verify_duration


@runtime_checkable
class Hasher(Protocol):
    def hash(self, plain: str) -> str: ...
    def verify(self, plain: str, encoded: str) -> bool: ...


class Argon2idParams(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    time: int = Field(default=3, validation_alias="ARGON2_TIME")
    memory_kib: int = Field(default=65536, validation_alias="ARGON2_MEMORY_KIB")
    threads: int = Field(default=4, validation_alias="ARGON2_THREADS")
    key_len: int = Field(default=32, validation_alias="ARGON2_KEYLEN")
    salt_len: int = Field(default=16, validation_alias="ARGON2_SALTLEN")


class Argon2idHasher:
    def __init__(self, params: Argon2idParams) -> None:
        self._ph = PasswordHasher(
            time_cost=params.time,
            memory_cost=params.memory_kib,
            parallelism=params.threads,
            hash_len=params.key_len,
            salt_len=params.salt_len,
        )

    def hash(self, plain: str) -> str:
        start = time.perf_counter()
        try:
            return self._ph.hash(plain)
        finally:
            record_hash_duration(time.perf_counter() - start)

    def verify(self, plain: str, encoded: str) -> bool:
        start = time.perf_counter()
        try:
            return self._ph.verify(encoded, plain)
        except (VerifyMismatchError, InvalidHashError, VerificationError):
            return False
        finally:
            record_verify_duration(time.perf_counter() - start)
