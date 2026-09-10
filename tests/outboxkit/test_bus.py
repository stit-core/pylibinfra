"""Insert-сторона: сериализация payload, метрики вставки и проброс ошибки хранилища."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from pylibinfra.outboxkit import Bus, OutboxError, Publisher, encode_payload

from .fakes import FakeRepo

_INSTANCE = "outbox"


def _bus(repo: FakeRepo) -> Bus:
    return Bus(_INSTANCE, repo)


async def test_publish_serializes_payload_and_counts_insert(
    read_metric: Callable[..., float],
) -> None:
    repo = FakeRepo()
    publisher: Publisher = _bus(repo)

    await publisher.publish("order.created", {"id": 42})

    assert repo.inserted == [("order.created", b'{"id":42}')]
    assert (
        read_metric(
            "outbox_events_inserted_total",
            {"instance": _INSTANCE, "subject": "order.created", "status": "ok"},
        )
        == 1
    )


async def test_bytes_payload_is_stored_as_is() -> None:
    repo = FakeRepo()

    await _bus(repo).publish("order.created", b'{"raw": true}')

    assert repo.inserted == [("order.created", b'{"raw": true}')]


async def test_unserializable_payload_never_reaches_store(
    read_metric: Callable[..., float],
) -> None:
    repo = FakeRepo()

    with pytest.raises(OutboxError, match="marshal payload"):
        await _bus(repo).publish("order.created", object())

    assert repo.inserted == []
    assert read_metric("outbox_events_inserted_total", {"status": "marshal_error"}) == 1


async def test_store_failure_is_counted_and_propagated(read_metric: Callable[..., float]) -> None:
    class _Broken(FakeRepo):
        async def insert(self, subject: str, payload: bytes) -> None:
            raise RuntimeError("no tx")

    with pytest.raises(RuntimeError, match="no tx"):
        await _bus(_Broken()).publish("order.created", {"id": 1})

    assert read_metric("outbox_events_inserted_total", {"status": "error"}) == 1


def test_encode_payload_is_compact() -> None:
    assert encode_payload({"a": 1, "b": "x"}) == b'{"a":1,"b":"x"}'
    assert encode_payload(bytearray(b"raw")) == b"raw"
