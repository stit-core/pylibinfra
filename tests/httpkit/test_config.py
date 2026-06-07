import pytest

from pylibinfra.httpkit import Config, new_config


def test_defaults() -> None:
    cfg = Config(_env_file=None)
    assert cfg.addr == ":8080"
    assert cfg.read_timeout == 15.0
    assert cfg.write_timeout == 30.0
    assert cfg.idle_timeout == 60.0
    assert cfg.shutdown_timeout == 15.0
    assert cfg.api_title == "API"
    assert cfg.api_version == "0.1.0"
    assert cfg.max_body.max_bytes == 1048576
    assert cfg.rate_limit.rps == 100
    assert cfg.rate_limit.burst == 200


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTP_ADDR", ":9090")
    monkeypatch.setenv("HTTP_READ_TIMEOUT", "5")
    monkeypatch.setenv("HTTP_API_TITLE", "MyAPI")
    monkeypatch.setenv("HTTP_MAX_BODY_BYTES", "2048")
    monkeypatch.setenv("HTTP_RATE_LIMIT_RPS", "50")
    monkeypatch.setenv("HTTP_CORS_ORIGINS", "https://a.example,https://b.example")
    cfg = new_config()
    assert cfg.addr == ":9090"
    assert cfg.read_timeout == 5.0
    assert cfg.api_title == "MyAPI"
    assert cfg.max_body.max_bytes == 2048
    assert cfg.rate_limit.rps == 50
    assert cfg.cors.origin_list == ["https://a.example", "https://b.example"]


def test_security_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTP_CSP", "default-src 'self'")
    monkeypatch.setenv("HTTP_HSTS", "max-age=1")
    cfg = new_config()
    assert cfg.security.csp == "default-src 'self'"
    assert cfg.security.hsts == "max-age=1"
