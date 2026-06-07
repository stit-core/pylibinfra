import pytest

from pylibinfra.authnkit import Argon2idParams, JWTConfig


def test_argon2_params_defaults() -> None:
    p = Argon2idParams()
    assert p.time == 3
    assert p.memory_kib == 65536
    assert p.threads == 4
    assert p.key_len == 32
    assert p.salt_len == 16


def test_argon2_params_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGON2_TIME", "1")
    monkeypatch.setenv("ARGON2_MEMORY_KIB", "8")
    monkeypatch.setenv("ARGON2_THREADS", "1")
    p = Argon2idParams()
    assert p.time == 1
    assert p.memory_kib == 8
    assert p.threads == 1


def test_jwt_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "s3cr3t")
    monkeypatch.setenv("JWT_ACCESS_TTL", "300")
    cfg = JWTConfig()
    assert cfg.secret == "s3cr3t"
    assert cfg.access_ttl == 300.0
    assert cfg.refresh_ttl == 2592000.0
