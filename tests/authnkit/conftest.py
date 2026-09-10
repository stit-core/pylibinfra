"""Изоляция тестов кита от ARGON2_*/JWT_*/TOTP_* в окружении разработчика."""

import pytest

_ENV = (
    "ARGON2_TIME",
    "ARGON2_MEMORY_KIB",
    "ARGON2_THREADS",
    "ARGON2_KEYLEN",
    "ARGON2_SALTLEN",
    "JWT_SECRET",
    "JWT_ACCESS_TTL",
    "JWT_REFRESH_TTL",
    "JWT_LEEWAY",
    "TOTP_ISSUER",
    "TOTP_SKEW",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
