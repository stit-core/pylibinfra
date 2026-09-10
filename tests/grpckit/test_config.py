import pytest
from pydantic import ValidationError

from pylibinfra.grpckit import Config, ConfigError, new_config


def test_defaults_mirror_golibinfra() -> None:
    cfg = Config()

    assert cfg.addr == ":9090"
    assert cfg.uds_path == ""
    assert (cfg.max_recv_msg_size, cfg.max_send_msg_size) == (4194304, 4194304)
    assert cfg.max_concurrent_streams == 0
    assert cfg.reflection is False
    assert cfg.shutdown_grace == 10.0
    assert (cfg.health_interval, cfg.health_probe_timeout) == (10.0, 5.0)
    assert (cfg.tls_cert_file, cfg.tls_key_file) == ("", "")
    assert (cfg.keepalive_time, cfg.keepalive_timeout) == (7200.0, 20.0)
    assert cfg.keepalive_min_time == 20.0
    assert cfg.keepalive_permit_without_stream is True


def test_env_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    for env, value in {
        "GRPC_ADDR": "0.0.0.0:50051",
        "GRPC_UDS_PATH": "/run/endpoint.sock",
        "GRPC_MAX_RECV_MSG_SIZE": "1024",
        "GRPC_MAX_SEND_MSG_SIZE": "2048",
        "GRPC_MAX_CONCURRENT_STREAMS": "64",
        "GRPC_REFLECTION": "true",
        "GRPC_SHUTDOWN_GRACE": "5",
        "GRPC_HEALTH_INTERVAL": "30",
        "GRPC_HEALTH_PROBE_TIMEOUT": "3",
        "GRPC_KEEPALIVE_TIME": "60",
        "GRPC_KEEPALIVE_TIMEOUT": "10",
        "GRPC_KEEPALIVE_MIN_TIME": "15",
        "GRPC_KEEPALIVE_PERMIT_WITHOUT_STREAM": "false",
    }.items():
        monkeypatch.setenv(env, value)

    cfg = new_config()

    assert cfg.addr == "0.0.0.0:50051"
    assert cfg.uds_path == "/run/endpoint.sock"
    assert (cfg.max_recv_msg_size, cfg.max_send_msg_size) == (1024, 2048)
    assert cfg.max_concurrent_streams == 64
    assert cfg.reflection is True
    assert cfg.shutdown_grace == 5.0
    assert (cfg.health_interval, cfg.health_probe_timeout) == (30.0, 3.0)
    assert (cfg.keepalive_time, cfg.keepalive_timeout, cfg.keepalive_min_time) == (60.0, 10.0, 15.0)
    assert cfg.keepalive_permit_without_stream is False


@pytest.mark.parametrize(
    "env",
    [
        "GRPC_MAX_RECV_MSG_SIZE",
        "GRPC_MAX_SEND_MSG_SIZE",
        "GRPC_MAX_CONCURRENT_STREAMS",
        "GRPC_SHUTDOWN_GRACE",
        "GRPC_HEALTH_INTERVAL",
        "GRPC_HEALTH_PROBE_TIMEOUT",
        "GRPC_KEEPALIVE_TIME",
        "GRPC_KEEPALIVE_TIMEOUT",
        "GRPC_KEEPALIVE_MIN_TIME",
    ],
)
def test_negative_values_rejected(env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(env, "-1")

    with pytest.raises(ConfigError, match=f"{env} must be >= 0"):
        new_config()


@pytest.mark.parametrize("env", ["GRPC_TLS_CERT_FILE", "GRPC_TLS_KEY_FILE"])
def test_half_tls_pair_rejected(env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(env, "/etc/tls/half.pem")

    with pytest.raises(ConfigError, match="must be set both or neither"):
        new_config()


def test_full_tls_pair_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRPC_TLS_CERT_FILE", "/etc/tls/server.pem")
    monkeypatch.setenv("GRPC_TLS_KEY_FILE", "/etc/tls/server.key")

    assert new_config().tls_cert_file == "/etc/tls/server.pem"


def test_probe_timeout_over_interval_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRPC_HEALTH_INTERVAL", "3")
    monkeypatch.setenv("GRPC_HEALTH_PROBE_TIMEOUT", "5")

    with pytest.raises(ConfigError, match="GRPC_HEALTH_PROBE_TIMEOUT must not exceed"):
        new_config()


def test_probe_timeout_free_when_loop_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRPC_HEALTH_INTERVAL", "0")
    monkeypatch.setenv("GRPC_HEALTH_PROBE_TIMEOUT", "5")

    assert new_config().health_interval == 0.0


def test_nothing_to_bind_rejected() -> None:
    with pytest.raises(ValidationError, match="nothing to bind"):
        Config(addr="", uds_path="")


def test_uds_only_is_valid() -> None:
    assert Config(addr="", uds_path="/run/x.sock").uds_path == "/run/x.sock"


def test_empty_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # env_ignore_empty: заданная пустой переменная читается как незаданная.
    monkeypatch.setenv("GRPC_ADDR", "")

    assert new_config().addr == ":9090"


def test_config_error_is_value_error() -> None:
    assert issubclass(ConfigError, ValueError)


def test_config_is_frozen() -> None:
    cfg = Config()

    with pytest.raises(ValidationError):
        cfg.addr = ":1"


def test_unparsable_value_reports_field_without_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRPC_SHUTDOWN_GRACE", "soon")

    with pytest.raises(ConfigError) as exc_info:
        new_config()

    message = str(exc_info.value)
    assert "grpckit: invalid config" in message
    assert "soon" not in message
