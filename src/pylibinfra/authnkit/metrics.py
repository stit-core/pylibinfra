"""Singleton-инструменты authnkit (как metrics.go в golibinfra).

Инструменты создаются один раз: повторные NewHS256JWT/Argon2idHasher не должны
пересоздавать одни и те же имена метрик в OTel SDK. Meter берётся из глобального
OTel-provider'а (его выставляет telemetry.init) — без telemetry эмит — no-op.
"""

from opentelemetry import metrics
from opentelemetry.metrics import Counter, Histogram

_instr: "_AuthnInstruments | None" = None


class _AuthnInstruments:
    def __init__(self) -> None:
        meter = metrics.get_meter("app")
        self.jwt_verifications: Counter = meter.create_counter(
            "jwt_verifications_total",
            description="JWT verification outcomes",
        )
        self.hash_duration: Histogram = meter.create_histogram(
            "password_hash_duration_seconds",
            unit="s",
            description="Time to hash a password (argon2id)",
        )
        self.verify_duration: Histogram = meter.create_histogram(
            "password_verify_duration_seconds",
            unit="s",
            description="Time to verify a password against an argon2id hash",
        )
        self.totp_verifications: Counter = meter.create_counter(
            "totp_verifications_total",
            description="TOTP verification outcomes",
        )


def _get_instruments() -> _AuthnInstruments:
    global _instr
    if _instr is None:
        _instr = _AuthnInstruments()
    return _instr


def record_jwt_result(result: str) -> None:
    _get_instruments().jwt_verifications.add(1, {"result": result})


def record_totp_result(result: str) -> None:
    _get_instruments().totp_verifications.add(1, {"result": result})


def record_hash_duration(seconds: float) -> None:
    _get_instruments().hash_duration.record(seconds)


def record_verify_duration(seconds: float) -> None:
    _get_instruments().verify_duration.record(seconds)
