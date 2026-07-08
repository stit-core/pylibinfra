from __future__ import annotations

import pytest

from pylibinfra.natskit import Config


def test_defaults() -> None:
    cfg = Config()
    assert cfg.url == "nats://localhost:4222"
    assert cfg.connect_timeout == 5.0
    # Go-дефолт 60 (равняем публичный контракт на golibinfra)
    assert cfg.max_reconnects == 60
    assert cfg.backpressure.max_in_flight == 100
    assert cfg.jetstream.enabled is False
    assert cfg.jetstream.retention == "limits"
    # poison-дефолты выкл (как в Go): backstop бесконечный, NAK мгновенный, DLQ нет
    assert cfg.jetstream.max_deliver == 0
    assert cfg.jetstream.nak_backoff == 0.0
    assert cfg.jetstream.dlq_subject == ""
    # окно дедупа = Go 2m
    assert cfg.jetstream.duplicates == 120.0


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NATS_URL", "nats://example:4222")
    monkeypatch.setenv("NATS_MAX_IN_FLIGHT", "7")
    monkeypatch.setenv("NATS_MAX_RECONNECTS", "10")
    monkeypatch.setenv("NATS_JETSTREAM_ENABLED", "true")
    monkeypatch.setenv("NATS_JS_MAX_IN_FLIGHT", "9")
    monkeypatch.setenv("NATS_JS_MAX_DELIVER", "5")
    monkeypatch.setenv("NATS_JS_NAK_BACKOFF", "1.5")
    monkeypatch.setenv("NATS_JS_DLQ_SUBJECT", "DLQ.orders")
    monkeypatch.setenv("NATS_JS_DUPLICATES", "300")

    cfg = Config()
    assert cfg.url == "nats://example:4222"
    assert cfg.backpressure.max_in_flight == 7
    assert cfg.max_reconnects == 10
    assert cfg.jetstream.enabled is True
    assert cfg.jetstream.max_in_flight == 9
    assert cfg.jetstream.max_deliver == 5
    assert cfg.jetstream.nak_backoff == 1.5
    assert cfg.jetstream.dlq_subject == "DLQ.orders"
    assert cfg.jetstream.duplicates == 300.0
