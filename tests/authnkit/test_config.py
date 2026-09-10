from __future__ import annotations

import pytest

from pylibinfra.authnkit import (
    DEFAULT_LEEWAY,
    DEFAULT_REFRESH_TTL,
    Argon2idParams,
    ConfigError,
    JWTConfig,
    TOTPConfig,
    new_argon2id_params,
    new_jwt_config,
    new_totp_config,
)

ENV_SECRET = "env-secret-key-of-at-least-32-bytes!!!"


def test_argon2_defaults() -> None:
    params = Argon2idParams()
    assert (params.time, params.memory_kib, params.threads) == (3, 65536, 4)
    assert (params.key_len, params.salt_len) == (32, 16)


def test_argon2_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGON2_TIME", "2")
    monkeypatch.setenv("ARGON2_MEMORY_KIB", "16384")

    params = new_argon2id_params()
    assert (params.time, params.memory_kib) == (2, 16384)
    assert (params.threads, params.key_len, params.salt_len) == (4, 32, 16)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ARGON2_TIME", "0"),
        ("ARGON2_TIME", "65"),
        ("ARGON2_TIME", "not-a-number"),
        ("ARGON2_MEMORY_KIB", "1024"),
        ("ARGON2_THREADS", "17"),
        ("ARGON2_KEYLEN", "8"),
        ("ARGON2_SALTLEN", "4"),
    ],
)
def test_argon2_from_env_rejects(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError):
        new_argon2id_params()


def test_argon2_cost_ceiling_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Каждый параметр в окне, произведение — нет: калибровка валит старт.
    monkeypatch.setenv("ARGON2_TIME", "4")
    monkeypatch.setenv("ARGON2_MEMORY_KIB", "262144")
    with pytest.raises(ConfigError):
        new_argon2id_params()


def test_jwt_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", ENV_SECRET)
    monkeypatch.setenv("JWT_ACCESS_TTL", "300")

    cfg = new_jwt_config()
    assert cfg.secret.get_secret_value() == ENV_SECRET
    assert cfg.access_ttl == 300
    assert cfg.refresh_ttl == DEFAULT_REFRESH_TTL
    assert cfg.leeway == DEFAULT_LEEWAY


def test_jwt_requires_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(ConfigError):
        new_jwt_config()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("JWT_SECRET", "short"),
        ("JWT_ACCESS_TTL", "0"),
        ("JWT_ACCESS_TTL", "not-a-number"),
        ("JWT_REFRESH_TTL", "-1"),
        ("JWT_LEEWAY", "-1"),
    ],
)
def test_jwt_from_env_rejects(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv("JWT_SECRET", ENV_SECRET)
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError):
        new_jwt_config()


def test_jwt_config_error_hides_the_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "short-but-secret")
    with pytest.raises(ConfigError) as exc:
        new_jwt_config()
    assert "short-but-secret" not in str(exc.value)
    # Локация ошибки — имя переменной окружения, а не поля модели.
    assert "authnkit: invalid config: JWT_SECRET" in str(exc.value)


def test_totp_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOTP_ISSUER", "My Service")

    cfg = new_totp_config()
    assert cfg.issuer == "My Service"
    assert cfg.skew == 1


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TOTP_SKEW", "10"),
        ("TOTP_SKEW", "not-a-number"),
        ("TOTP_ISSUER", "My:Service"),
        ("TOTP_ISSUER", "My/Service"),
    ],
)
def test_totp_from_env_rejects(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv("TOTP_ISSUER", "My Service")
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError):
        new_totp_config()


def test_totp_requires_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOTP_ISSUER", raising=False)
    with pytest.raises(ConfigError):
        new_totp_config()


def test_configs_are_frozen() -> None:
    cfg = TOTPConfig(issuer="svc")
    with pytest.raises(ValueError, match="frozen"):
        cfg.skew = 2  # type: ignore[misc]

    jwt_cfg = JWTConfig(secret=ENV_SECRET)
    with pytest.raises(ValueError, match="frozen"):
        jwt_cfg.leeway = 1.0  # type: ignore[misc]
