import pytest

from pylibinfra.pgkit import MigrateConfig, PoolConfig, ReplicaConfig
from pylibinfra.pgkit.module import new_pool_config, new_replica_config


def test_pool_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_DSN", "postgresql://localhost/app")
    cfg = new_pool_config()
    assert cfg.dsn == "postgresql://localhost/app"
    assert cfg.max_conns == 10
    assert cfg.min_conns == 2
    assert cfg.max_conn_lifetime == 1800.0
    assert cfg.otel_trace is True
    assert cfg.statement_timeout == 30.0


def test_pool_config_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_DSN", "postgresql://db/app")
    monkeypatch.setenv("PG_MAX_CONNS", "25")
    monkeypatch.setenv("PG_MIN_CONNS", "4")
    monkeypatch.setenv("PG_OTEL_TRACE", "false")
    monkeypatch.setenv("PG_LOCK_TIMEOUT", "2.5")
    cfg = PoolConfig()
    assert cfg.max_conns == 25
    assert cfg.min_conns == 4
    assert cfg.otel_trace is False
    assert cfg.lock_timeout == 2.5


def test_replica_config_defaults() -> None:
    cfg = new_replica_config()
    assert cfg.replica_dsn == ""
    assert cfg.max_conns == 10


def test_replica_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_REPLICA_DSN", "postgresql://replica/app")
    monkeypatch.setenv("PG_REPLICA_MAX_CONNS", "7")
    cfg = ReplicaConfig()
    assert cfg.replica_dsn == "postgresql://replica/app"
    assert cfg.max_conns == 7


def test_migrate_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_DSN", "postgresql://db/app")
    monkeypatch.setenv("PG_MIGRATIONS_PATH", "/srv/migrations")
    cfg = MigrateConfig()
    assert cfg.dsn == "postgresql://db/app"
    assert cfg.path == "/srv/migrations"
