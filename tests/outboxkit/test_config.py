"""Env-контракт outboxkit и его pgstore: дефолты и fail-fast валидация."""

from __future__ import annotations

import pytest

from pylibinfra.outboxkit import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_INSTANCE,
    DEFAULT_TICK_TIMEOUT,
    Config,
    ConfigError,
    PublishMode,
    new_config,
)
from pylibinfra.outboxkit.pgstore import Config as PgstoreConfig
from pylibinfra.outboxkit.pgstore import new_config as new_pgstore_config
from pylibinfra.outboxkit.worker import COMMIT_GRACE
from pylibinfra.pgkit import PoolConfig

_ENV = [
    "OUTBOX_BATCH_SIZE",
    "OUTBOX_INTERVAL",
    "OUTBOX_PUBLISH_MODE",
    "OUTBOX_MAX_ATTEMPTS",
    "OUTBOX_TICK_TIMEOUT",
    "OUTBOX_RETRY_BACKOFF",
    "OUTBOX_RETRY_MAX_BACKOFF",
    "OUTBOX_RETENTION_MONTHS",
    "OUTBOX_REQUIRE_TX",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_safe() -> None:
    cfg = new_config()

    assert cfg.batch_size == DEFAULT_BATCH_SIZE == 100
    assert cfg.interval == 1.0
    assert cfg.publish_mode == PublishMode.BATCH
    assert cfg.max_attempts == 0
    assert cfg.tick_timeout == DEFAULT_TICK_TIMEOUT == 30.0
    assert cfg.retry_backoff == 1.0
    assert cfg.retry_max_backoff == 300.0
    assert DEFAULT_INSTANCE == "outbox"


def test_tick_budget_fits_pgkit_idle_in_transaction_default() -> None:
    # Пока тик ждёт PubAck, транзакция простаивает: сессию оборвал бы Postgres.
    idle_in_tx = PoolConfig.model_fields["idle_in_tx_session_timeout"].default

    assert idle_in_tx > DEFAULT_TICK_TIMEOUT + COMMIT_GRACE


def test_pgstore_defaults_require_transaction() -> None:
    cfg = new_pgstore_config()

    assert cfg.require_tx is True
    assert cfg.retention_months == 0


def test_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTBOX_BATCH_SIZE", "5")
    monkeypatch.setenv("OUTBOX_PUBLISH_MODE", "ordered")
    monkeypatch.setenv("OUTBOX_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("OUTBOX_RETENTION_MONTHS", "6")
    monkeypatch.setenv("OUTBOX_REQUIRE_TX", "false")

    cfg = new_config()
    pg = new_pgstore_config()

    assert cfg.batch_size == 5
    assert cfg.publish_mode == PublishMode.ORDERED
    assert cfg.max_attempts == 3
    assert pg.retention_months == 6
    assert pg.require_tx is False


def test_empty_env_value_means_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTBOX_TICK_TIMEOUT", "")
    monkeypatch.setenv("OUTBOX_REQUIRE_TX", "")

    assert new_config().tick_timeout == DEFAULT_TICK_TIMEOUT
    assert new_pgstore_config().require_tx is True


@pytest.mark.parametrize(
    ("env", "value", "message"),
    [
        ("OUTBOX_BATCH_SIZE", "0", "OUTBOX_BATCH_SIZE"),
        ("OUTBOX_INTERVAL", "0", "OUTBOX_INTERVAL"),
        ("OUTBOX_PUBLISH_MODE", "fifo", "OUTBOX_PUBLISH_MODE"),
        # 0 больше не значит «без дедлайна»: тик идёт мимо отмены вызывающего.
        ("OUTBOX_TICK_TIMEOUT", "0", "OUTBOX_TICK_TIMEOUT"),
        ("OUTBOX_MAX_ATTEMPTS", "-1", "OUTBOX_MAX_ATTEMPTS"),
        ("OUTBOX_RETRY_BACKOFF", "-1", "OUTBOX_RETRY_BACKOFF"),
    ],
)
def test_invalid_values_fail_fast(
    monkeypatch: pytest.MonkeyPatch, env: str, value: str, message: str
) -> None:
    monkeypatch.setenv(env, value)

    with pytest.raises(ConfigError, match=message):
        new_config()


def test_backoff_ceiling_must_not_be_below_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTBOX_RETRY_BACKOFF", "10")
    monkeypatch.setenv("OUTBOX_RETRY_MAX_BACKOFF", "5")

    with pytest.raises(ConfigError, match="OUTBOX_RETRY_MAX_BACKOFF"):
        new_config()


def test_pgstore_retention_must_not_be_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTBOX_RETENTION_MONTHS", "-1")

    with pytest.raises(ConfigError, match="OUTBOX_RETENTION_MONTHS"):
        new_pgstore_config()


def test_direct_construction_validates() -> None:
    # Прямой конструктор отдаёт pydantic ValidationError (тоже ValueError), а не ConfigError.
    with pytest.raises(ValueError, match="batch_size"):
        Config(batch_size=0)
    with pytest.raises(ValueError, match="require_tx"):
        PgstoreConfig(require_tx="maybe")  # type: ignore[arg-type]
