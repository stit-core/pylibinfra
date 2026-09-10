"""Config кита и канонизация ключа `(subject, ip)`.

Сила guard'а равна доверию к `ip`: адрес из подделываемого заголовка даёт новую
строку на каждую попытку, и блокировка не наступает никогда. Канонизацию делает
кит — приводить адрес заранее вызывающему не нужно.
"""

from __future__ import annotations

import ipaddress
from typing import Self

from pydantic import Field, model_validator

from pylibinfra._internal import configkit
from pylibinfra._internal.configkit import KitSettings

# Предел длины subject: значение длиннее не помещается в btree-индекс Postgres
# вместе с ip, и запрос падает уже в базе.
MAX_SUBJECT_LEN = 255

FULL_IPV6_PREFIX = 128


class ConfigError(configkit.ConfigError):
    """Невалидный конфиг кита; нулевой Config отвергается, а не выключает guard молча."""


class InvalidKeyError(ValueError):
    """Пустой или слишком длинный subject, пустой либо неразбираемый ip."""


class Config(KitSettings):
    """Политика guard'а: порог, окно блокировки, чистка и агрегация IPv6."""

    model_config = configkit.settings_config()

    max_failures: int = Field(default=5, validation_alias="BRUTEFORCE_MAX_FAILURES")
    lock_duration: float = Field(default=900.0, validation_alias="BRUTEFORCE_LOCK_DURATION")
    cleanup_retention: float = Field(
        default=86400.0, validation_alias="BRUTEFORCE_CLEANUP_RETENTION"
    )
    cleanup_interval: float = Field(default=300.0, validation_alias="BRUTEFORCE_CLEANUP_INTERVAL")
    ipv6_prefix: int = Field(default=FULL_IPV6_PREFIX, validation_alias="BRUTEFORCE_IPV6_PREFIX")

    @model_validator(mode="after")
    def _check(self) -> Self:
        """Проверяет инварианты; текст ошибки называет ENV-переменную, а не поле."""
        if self.max_failures < 1:
            raise ValueError(f"BRUTEFORCE_MAX_FAILURES must be >= 1, got {self.max_failures}")
        if self.lock_duration <= 0:
            raise ValueError(f"BRUTEFORCE_LOCK_DURATION must be > 0, got {self.lock_duration:g}")
        if self.cleanup_retention < 0:
            raise ValueError(
                f"BRUTEFORCE_CLEANUP_RETENTION must be >= 0, got {self.cleanup_retention:g}"
            )
        if self.cleanup_interval <= 0:
            raise ValueError(
                f"BRUTEFORCE_CLEANUP_INTERVAL must be > 0, got {self.cleanup_interval:g}"
            )
        if not 1 <= self.ipv6_prefix <= FULL_IPV6_PREFIX:
            raise ValueError(
                f"BRUTEFORCE_IPV6_PREFIX must be in [1, {FULL_IPV6_PREFIX}], got {self.ipv6_prefix}"
            )
        return self

    def normalize_key(self, subject: str, ip: str) -> tuple[str, str]:
        """Проверяет subject и приводит ip к канонической форме ключа.

        Разные записи одного адреса (`::1` и `0:0:0:0:0:0:0:1`,
        `::ffff:203.0.113.7` и `203.0.113.7`) обязаны попадать в один бакет
        счётчика; зона (`fe80::1%eth0`) значима только на хосте отправителя и
        отбрасывается. IPv6 при `ipv6_prefix < 128` сворачивается до подсети.
        Нарушение — `InvalidKeyError`; регистр и форму subject кит не трогает.
        """
        if not subject:
            raise InvalidKeyError("bruteforcekit: invalid key: subject is empty")
        if len(subject) > MAX_SUBJECT_LEN:
            raise InvalidKeyError(
                f"bruteforcekit: invalid key: subject is longer than {MAX_SUBJECT_LEN} characters"
            )
        if not ip:
            raise InvalidKeyError("bruteforcekit: invalid key: ip is empty")

        try:
            addr = ipaddress.ip_address(ip)
        except ValueError as exc:
            raise InvalidKeyError(
                f"bruteforcekit: invalid key: ip {ip!r} is not an IP address"
            ) from exc

        if isinstance(addr, ipaddress.IPv6Address):
            if addr.scope_id is not None:
                addr = ipaddress.IPv6Address(addr.packed)
            mapped = addr.ipv4_mapped
            if mapped is not None:
                addr = mapped

        if isinstance(addr, ipaddress.IPv6Address) and self.ipv6_prefix < FULL_IPV6_PREFIX:
            network = ipaddress.ip_network(f"{addr}/{self.ipv6_prefix}", strict=False)
            return subject, str(network.network_address)
        return subject, str(addr)


def new_config() -> Config:
    """Читает `Config` из ENV; нарушение инварианта — `bruteforcekit.ConfigError`."""
    return configkit.build(Config, "bruteforcekit", ConfigError)
