"""OTel-инструменты кита; создаются в конструкторах примитивов, не на импорте.

Инструмент, созданный на уровне модуля, привязался бы к провайдеру, который был
глобальным в момент импорта, — тест с новым `MeterProvider` его бы не увидел.
"""

from __future__ import annotations

from opentelemetry.metrics import Counter, Histogram

from pylibinfra._internal import otelkit

from .errors import KIT


class HashInstruments:
    """Гистограммы времени `Argon2idHasher.hash` и `verify`/`verify_dummy`."""

    def __init__(self) -> None:
        meter = otelkit.meter(KIT)
        self.hash_duration: Histogram = otelkit.duration_histogram(
            meter,
            "authn_password_hash_duration_seconds",
            "Time to hash a password (argon2id)",
        )
        self.verify_duration: Histogram = otelkit.duration_histogram(
            meter,
            "authn_password_verify_duration_seconds",
            "Time to verify a password against an argon2id hash",
        )


class JWTInstruments:
    """Счётчик исходов разбора JWT."""

    def __init__(self) -> None:
        self.verifications: Counter = otelkit.meter(KIT).create_counter(
            "authn_jwt_verifications_total",
            description="JWT verification outcomes",
        )

    def record(self, result: str) -> None:
        """Инкрементит `authn_jwt_verifications_total{result}`."""
        self.verifications.add(1, {"result": result})


class TOTPInstruments:
    """Счётчик исходов проверки TOTP-кода."""

    def __init__(self) -> None:
        self.verifications: Counter = otelkit.meter(KIT).create_counter(
            "authn_totp_verifications_total",
            description="TOTP verification outcomes",
        )

    def record(self, result: str) -> None:
        """Инкрементит `authn_totp_verifications_total{result}`."""
        self.verifications.add(1, {"result": result})
