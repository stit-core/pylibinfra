"""Опции публикации, маркер `TerminalError` и JSON-хелперы без соединения."""

from __future__ import annotations

import pytest

from pylibinfra.natskit import (
    MSG_ID_HEADER,
    PublishError,
    StreamMsg,
    TerminalError,
    is_terminal,
    new_publish_spec,
)
from pylibinfra.natskit.bus import decode_json, encode_json


def test_wire_headers_add_msg_id_and_copy_input() -> None:
    headers = {"Event-Type": "order.created"}
    spec = new_publish_spec(msg_id="42", headers=headers, metric_subject="orders.*")

    wire = spec.wire_headers()

    assert wire == {"Event-Type": "order.created", MSG_ID_HEADER: "42"}
    wire["traceparent"] = "00-x"
    assert headers == {"Event-Type": "order.created"}
    assert spec.metric_subject == "orders.*"


def test_publish_spec_without_options_is_empty() -> None:
    spec = new_publish_spec()

    assert spec.msg_id == ""
    assert spec.metric_subject == ""
    assert spec.wire_headers() == {}


def test_stream_msg_spec_carries_publish_options() -> None:
    msg = StreamMsg(
        subject="ORDERS.created",
        payload=b"{}",
        msg_id="7",
        headers={"X": "1"},
        metric_subject="outbox",
    )

    spec = msg.spec()

    assert spec.msg_id == "7"
    assert spec.metric_subject == "outbox"
    assert spec.wire_headers() == {"X": "1", MSG_ID_HEADER: "7"}


def test_is_terminal_follows_cause_chain() -> None:
    root = TerminalError("natskit: bad schema")
    wrapped = RuntimeError("handler failed")
    wrapped.__cause__ = root

    assert is_terminal(root)
    assert is_terminal(wrapped)
    assert not is_terminal(RuntimeError("transient"))
    assert not is_terminal(None)


def test_is_terminal_survives_cycles() -> None:
    first = RuntimeError("a")
    second = RuntimeError("b")
    first.__cause__ = second
    second.__cause__ = first

    assert not is_terminal(first)


def test_encode_json_rejects_unserializable() -> None:
    assert encode_json({"a": 1}) == b'{"a":1}'
    with pytest.raises(PublishError, match="marshal payload"):
        encode_json(object())


async def test_decode_json_marks_broken_payload_terminal() -> None:
    seen: list[dict[str, int]] = []

    async def handler(msg: dict[str, int]) -> None:
        seen.append(msg)

    wrapped = decode_json(handler)
    await wrapped(b'{"id": 1}')
    assert seen == [{"id": 1}]

    with pytest.raises(TerminalError) as excinfo:
        await wrapped(b"{oops")
    assert is_terminal(excinfo.value)
