"""Соединение без сервера: тексты ошибок, health-состояния и логи событий nats-py."""

from __future__ import annotations

import pytest
from nats.aio.client import Client
from nats.errors import SlowConsumerError

from pylibinfra.natskit import Config, Conn, ConnectionClosedError, NatskitError, new_conn
from pylibinfra.natskit.conn import _callbacks
from pylibinfra.testkit.logtest import Capture

_UNREACHABLE = "nats://user:s3cret@127.0.0.1:4223"


async def test_connect_failure_hides_userinfo() -> None:
    cfg = Config(url=_UNREACHABLE, connect_timeout=0.2, max_reconnects=0)

    with pytest.raises(NatskitError) as excinfo:
        await new_conn(cfg)

    message = str(excinfo.value)
    assert "s3cret" not in message
    assert "nats://xxxxx@127.0.0.1:4223" in message


async def test_connect_timeout_is_bounded_for_infinite_reconnects() -> None:
    # NATS_MAX_RECONNECTS=-1 не должен держать старт сервиса бесконечно.
    cfg = Config(url="nats://127.0.0.1:4223", connect_timeout=0.2)

    with pytest.raises(NatskitError, match="connect"):
        await new_conn(cfg)


async def test_healthy_reports_transport_state() -> None:
    conn = Conn(Client(), Config(url=_UNREACHABLE), Capture())

    assert conn.status() == "disconnected"
    with pytest.raises(NatskitError, match="not connected"):
        conn.healthy()


async def test_closed_without_close_is_reported_as_exhausted_reconnects() -> None:
    log = Capture()
    conn = Conn(Client(), Config(url=_UNREACHABLE), log, instance="hub")

    conn.on_closed()

    entry = log.find("natskit: connection closed, reconnects exhausted", level="error")
    assert entry is not None
    assert entry.str("url") == "nats://xxxxx@127.0.0.1:4223"
    assert entry.str("instance") == "hub"


async def test_closed_during_close_is_silent() -> None:
    log = Capture()
    conn = Conn(Client(), Config(url=_UNREACHABLE), log)

    await conn.close(timeout=0.1)
    log.reset()
    conn.on_closed()

    assert not log.has("natskit: connection closed, reconnects exhausted", level="error")
    with pytest.raises(ConnectionClosedError):
        # После close() соединение отдаёт ошибку, отличимую от RECONNECTING.
        Conn(_closed_client(), Config(url=_UNREACHABLE), log).healthy()


async def test_connection_callbacks_log_redacted_events() -> None:
    log = Capture()
    conn = Conn(Client(), Config(url=_UNREACHABLE), log)
    callbacks = _callbacks({"conn": conn}, log, "nats://xxxxx@127.0.0.1:4223")

    await callbacks["disconnected_cb"]()
    await callbacks["reconnected_cb"]()
    await callbacks["error_cb"](RuntimeError("protocol"))

    disconnected = log.find("natskit: disconnected", level="warn")
    assert disconnected is not None
    assert disconnected.str("url") == "nats://xxxxx@127.0.0.1:4223"
    assert log.has("natskit: reconnected", level="info")
    assert log.has("natskit: async error", level="error")


async def test_slow_consumer_is_logged_with_subject() -> None:
    log = Capture()
    conn = Conn(Client(), Config(url=_UNREACHABLE), log)
    callbacks = _callbacks({"conn": conn}, log, "nats://127.0.0.1:4223")
    sub = _FakeSub()

    await callbacks["error_cb"](SlowConsumerError("orders.created", "", 1, sub))  # type: ignore[arg-type]

    entry = log.find("natskit: async error", level="error")
    assert entry is not None
    assert entry.str("subject") == "orders.created"
    assert entry.value("pending") == 7


def _closed_client() -> Client:
    client = Client()
    client._status = Client.CLOSED
    return client


class _FakeSub:
    pending_msgs = 7
