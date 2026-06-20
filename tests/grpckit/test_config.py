import pytest

from pylibinfra.grpckit import Config, new_config


def test_defaults() -> None:
    cfg = Config()
    assert cfg.addr == ""
    assert cfg.uds_path == ""
    assert cfg.shutdown_grace == 15.0
    assert cfg.max_recv_bytes == 16 * 1024 * 1024
    assert cfg.max_send_bytes == 16 * 1024 * 1024


def test_env_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRPC_ADDR", "0.0.0.0:50051")
    monkeypatch.setenv("GRPC_UDS_PATH", "/run/endpoint.sock")
    monkeypatch.setenv("GRPC_SHUTDOWN_GRACE", "5")
    monkeypatch.setenv("GRPC_MAX_RECV_BYTES", "1024")
    cfg = new_config()
    assert cfg.addr == "0.0.0.0:50051"
    assert cfg.uds_path == "/run/endpoint.sock"
    assert cfg.shutdown_grace == 5.0
    assert cfg.max_recv_bytes == 1024
