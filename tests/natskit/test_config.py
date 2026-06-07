from __future__ import annotations

import pytest

from pylibinfra.natskit import Config


def test_defaults() -> None:
    cfg = Config()
    assert cfg.url == "nats://localhost:4222"
    assert cfg.connect_timeout == 5.0
    # -1 = бесконечный reconnect: consumer-бот не умирает молча
    assert cfg.max_reconnects == -1
    assert cfg.backpressure.max_in_flight == 100
    assert cfg.jetstream.enabled is False
    assert cfg.jetstream.retention == "limits"


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NATS_URL", "nats://example:4222")
    monkeypatch.setenv("NATS_MAX_IN_FLIGHT", "7")
    monkeypatch.setenv("NATS_MAX_RECONNECTS", "10")
    monkeypatch.setenv("NATS_JETSTREAM_ENABLED", "true")
    monkeypatch.setenv("NATS_JS_MAX_IN_FLIGHT", "9")

    cfg = Config()
    assert cfg.url == "nats://example:4222"
    assert cfg.backpressure.max_in_flight == 7
    assert cfg.max_reconnects == 10
    assert cfg.jetstream.enabled is True
    assert cfg.jetstream.max_in_flight == 9
