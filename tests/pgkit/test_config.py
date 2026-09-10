import pytest
from pydantic import ValidationError

from pylibinfra.pgkit import (
    ConfigError,
    MigrateRunnerConfig,
    PoolConfig,
    ReplicaConfig,
    new_migrate_runner_config,
    new_pool_config,
    new_replica_config,
)

_PG_ENV = (
    "PG_DSN",
    "PG_MAX_CONNS",
    "PG_MIN_CONNS",
    "PG_MAX_CONN_IDLE_TIME",
    "PG_CONNECT_TIMEOUT",
    "PG_OTEL_TRACE",
    "PG_STATEMENT_TIMEOUT",
    "PG_IDLE_IN_TX_SESSION_TIMEOUT",
    "PG_LOCK_TIMEOUT",
    "PG_REPLICA_DSN",
    "PG_REPLICA_MAX_CONNS",
    "PG_MIGRATIONS_PATH",
    "PG_MIGRATE_TIMEOUT",
    # bare-имена полей — второй env-ключ pydantic-settings при populate_by_name
    "DSN",
    "MAX_CONNS",
    "MIN_CONNS",
    "CONNECT_TIMEOUT",
    "MIGRATIONS_PATH",
    "MIGRATE_TIMEOUT",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _PG_ENV:
        monkeypatch.delenv(name, raising=False)


def test_pool_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_DSN", "postgresql://localhost/app")
    cfg = new_pool_config()
    assert cfg.dsn.get_secret_value() == "postgresql://localhost/app"
    assert cfg.max_conns == 10
    assert cfg.min_conns == 2
    assert cfg.max_conn_idle_time == 300.0
    assert cfg.connect_timeout == 10.0
    assert cfg.otel_trace is True
    assert cfg.statement_timeout == 30.0
    assert cfg.idle_in_tx_session_timeout == 60.0
    assert cfg.lock_timeout == 5.0


def test_pool_config_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_DSN", "postgresql://db/app")
    monkeypatch.setenv("PG_MAX_CONNS", "25")
    monkeypatch.setenv("PG_MIN_CONNS", "4")
    monkeypatch.setenv("PG_OTEL_TRACE", "false")
    monkeypatch.setenv("PG_LOCK_TIMEOUT", "2.5")
    cfg = new_pool_config()
    assert cfg.max_conns == 25
    assert cfg.min_conns == 4
    assert cfg.otel_trace is False
    assert cfg.lock_timeout == 2.5


def test_pool_config_dsn_is_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_DSN", "postgresql://user:s3cret@db/app")
    cfg = new_pool_config()
    assert "s3cret" not in repr(cfg)
    assert "s3cret" not in str(cfg.dsn)
    assert cfg.dsn.get_secret_value().endswith("@db/app")


def test_pool_config_requires_dsn() -> None:
    with pytest.raises(ConfigError, match="pgkit: invalid config"):
        new_pool_config()


def test_pool_config_rejects_empty_dsn() -> None:
    # Прямой конструктор минует configkit.build и даёт pydantic ValidationError.
    with pytest.raises(ValidationError, match="PG_DSN is empty"):
        PoolConfig.model_validate({"dsn": ""})


@pytest.mark.parametrize(
    ("env", "value", "message"),
    [
        ("PG_MAX_CONNS", "-1", "PG_MAX_CONNS must be >= 0"),
        ("PG_MIN_CONNS", "-2", "PG_MIN_CONNS must be >= 0"),
        ("PG_CONNECT_TIMEOUT", "-1", "PG_CONNECT_TIMEOUT must be >= 0"),
        ("PG_STATEMENT_TIMEOUT", "-1", "PG_STATEMENT_TIMEOUT must be >= 0"),
        ("PG_LOCK_TIMEOUT", "-1", "PG_LOCK_TIMEOUT must be >= 0"),
        ("PG_IDLE_IN_TX_SESSION_TIMEOUT", "-1", "PG_IDLE_IN_TX_SESSION_TIMEOUT must be >= 0"),
        ("PG_MAX_CONN_IDLE_TIME", "-1", "PG_MAX_CONN_IDLE_TIME must be >= 0"),
    ],
)
def test_pool_config_rejects_negatives(
    monkeypatch: pytest.MonkeyPatch, env: str, value: str, message: str
) -> None:
    monkeypatch.setenv("PG_DSN", "postgresql://db/app")
    monkeypatch.setenv(env, value)
    with pytest.raises(ConfigError, match=message):
        new_pool_config()


def test_pool_config_min_must_fit_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_DSN", "postgresql://db/app")
    monkeypatch.setenv("PG_MAX_CONNS", "2")
    monkeypatch.setenv("PG_MIN_CONNS", "5")
    with pytest.raises(ConfigError, match=r"PG_MIN_CONNS \(5\) must be <= PG_MAX_CONNS \(2\)"):
        new_pool_config()


def test_pool_config_zero_max_conns_allows_any_min(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_DSN", "postgresql://db/app")
    monkeypatch.setenv("PG_MAX_CONNS", "0")
    monkeypatch.setenv("PG_MIN_CONNS", "5")
    assert new_pool_config().max_conns == 0


def test_server_settings_are_milliseconds() -> None:
    cfg = PoolConfig.model_validate(
        {
            "dsn": "postgresql://db/app",
            "statement_timeout": 7.0,
            "idle_in_tx_session_timeout": 0.0,
            "lock_timeout": 0.5,
        }
    )
    assert cfg.server_settings() == {
        "statement_timeout": "7000",
        "idle_in_transaction_session_timeout": "0",
        "lock_timeout": "500",
    }


def test_replica_config_defaults() -> None:
    cfg = new_replica_config()
    assert cfg.replica_dsn.get_secret_value() == ""
    assert cfg.max_conns == 10
    assert cfg.has_replica() is False


def test_replica_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_REPLICA_DSN", "postgresql://replica/app")
    monkeypatch.setenv("PG_REPLICA_MAX_CONNS", "7")
    cfg = new_replica_config()
    assert cfg.replica_dsn.get_secret_value() == "postgresql://replica/app"
    assert cfg.max_conns == 7
    assert cfg.has_replica() is True


def test_replica_config_rejects_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_REPLICA_MAX_CONNS", "-1")
    with pytest.raises(ConfigError, match="PG_REPLICA_MAX_CONNS must be >= 0"):
        new_replica_config()


def test_replica_pool_config_inherits_primary() -> None:
    base = PoolConfig.model_validate(
        {"dsn": "postgresql://master/app", "min_conns": 2, "statement_timeout": 11.0}
    )
    rep = ReplicaConfig.model_validate({"replica_dsn": "postgresql://replica/app", "max_conns": 4})
    derived = rep.pool_config(base)
    assert derived.dsn.get_secret_value() == "postgresql://replica/app"
    assert derived.max_conns == 4
    assert derived.min_conns == 2
    assert derived.statement_timeout == 11.0


def test_replica_max_conns_must_fit_primary_min() -> None:
    base = PoolConfig.model_validate({"dsn": "postgresql://master/app", "min_conns": 3})
    rep = ReplicaConfig.model_validate({"replica_dsn": "postgresql://r/app", "max_conns": 1})
    with pytest.raises(
        ConfigError, match=r"PG_REPLICA_MAX_CONNS \(1\) must be >= PG_MIN_CONNS \(3\)"
    ):
        rep.pool_config(base)


def test_migrate_runner_config_defaults() -> None:
    cfg = new_migrate_runner_config()
    assert cfg.migrations_path == "/migrations"
    assert cfg.migrate_timeout == 120.0


def test_migrate_runner_config_empty_path_means_no_app_migrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PG_MIGRATIONS_PATH", "")
    assert new_migrate_runner_config().migrations_path == ""


def test_migrate_runner_config_rejects_whitespace_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_MIGRATIONS_PATH", " /migrations ")
    with pytest.raises(ConfigError, match="PG_MIGRATIONS_PATH has surrounding whitespace"):
        new_migrate_runner_config()


def test_migrate_runner_config_rejects_nonpositive_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PG_MIGRATE_TIMEOUT", "0")
    with pytest.raises(ConfigError, match="PG_MIGRATE_TIMEOUT must be > 0"):
        new_migrate_runner_config()


def test_migrate_runner_config_ignores_system_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # Поле называется migrations_path, а не path: иначе populate_by_name подставил бы PATH.
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert MigrateRunnerConfig().migrations_path == "/migrations"
