"""Transport — порт публикации, через который Worker отдаёт события в брокер.

Контракт: возврат без ошибки — только после подтверждённого персиста (JetStream
PubAck); иначе Worker не метит событие published и переотправит следующим тиком.
publish — одиночный (ordered-режим), publish_batch — async-батч; results[i] is
None ⇒ i-е подтверждено. Реализация StreamTransport адаптирует natskit.StreamBus.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pylibinfra.natskit import StreamBus, StreamMsg


@dataclass(frozen=True, slots=True)
class OutMsg:
    """Событие outbox на публикацию. id становится Nats-Msg-Id: повторная
    публикация той же строки (ретрай тика) дедуплицируется стримом."""

    id: int
    subject: str
    payload: bytes


class Transport(Protocol):
    async def publish(self, msg: OutMsg) -> None: ...
    async def publish_batch(self, msgs: list[OutMsg]) -> list[Exception | None]: ...


class StreamTransport:
    def __init__(self, sb: StreamBus, namespace: str = "") -> None:
        self._sb = sb
        self._namespace = namespace

    def _msg_id(self, id: int) -> str:
        if not self._namespace:
            return str(id)
        return f"{self._namespace}:{id}"

    async def publish(self, msg: OutMsg) -> None:
        await self._sb.publish_stream(msg.subject, msg.payload, self._msg_id(msg.id))

    async def publish_batch(self, msgs: list[OutMsg]) -> list[Exception | None]:
        sm = [
            StreamMsg(subject=m.subject, payload=m.payload, msg_id=self._msg_id(m.id)) for m in msgs
        ]
        return await self._sb.publish_stream_batch(sm)


def new_stream_transport(sb: StreamBus) -> Transport:
    """Адаптирует natskit JetStream (StreamBus) под Transport. Nats-Msg-Id = id
    строки — корректно, пока outbox-таблица одна (BIGSERIAL глобально уникален).
    Несколько таблиц на один стрим → new_stream_transport_for."""
    return StreamTransport(sb)


def new_stream_transport_for(sb: StreamBus, namespace: str) -> Transport:
    """namespace'ит Nats-Msg-Id ("<namespace>:<id>") для нескольких outbox-инстансов,
    делящих один JetStream-стрим. Per-domain таблицы имеют независимый BIGSERIAL —
    голый id повторяется между таблицами, а stream-дедуп идёт по Msg-Id (не по
    subject) и молча уронил бы второй факт. namespace обычно = имя Outbox-инстанса."""
    return StreamTransport(sb, namespace)
