"""Transport — порт публикации в брокер и его JetStream-реализация поверх `natskit.StreamBus`.

Контракт: возврат без ошибки — только после подтверждённого персиста (`PubAck`),
иначе `Worker` не пометит событие published и переотправит его следующим тиком.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pylibinfra.natskit import StreamBus, StreamMsg

from .config import DEFAULT_INSTANCE


@dataclass(frozen=True, slots=True)
class OutMsg:
    """Событие outbox на публикацию; `id` становится `Nats-Msg-Id` (дедуп переотправки)."""

    id: int
    subject: str
    payload: bytes


class Transport(Protocol):
    """Порт публикации: одиночная (ordered) и батчевая (batch) отправка."""

    async def publish(self, msg: OutMsg) -> None:
        """Публикует одно событие; возврат без ошибки = персист подтверждён."""

    async def publish_batch(self, msgs: Sequence[OutMsg]) -> list[Exception | None]:
        """Публикует батч; `results[i] is None` — i-е событие подтверждено."""


class StreamTransport:
    """Адаптер `natskit.StreamBus` под `Transport` с namespace'ным `Nats-Msg-Id`."""

    def __init__(self, sb: StreamBus, namespace: str = "") -> None:
        self._sb = sb
        self._namespace = namespace
        # Доменный subject в label'е publish-метрик дал бы неограниченную кардинальность.
        self._metric_subject = namespace or DEFAULT_INSTANCE

    def _msg_id(self, row_id: int) -> str:
        return f"{self._namespace}:{row_id}" if self._namespace else str(row_id)

    async def publish(self, msg: OutMsg) -> None:
        """Публикует событие в стрим и ждёт `PubAck`."""
        await self._sb.publish_stream(
            msg.subject,
            msg.payload,
            msg_id=self._msg_id(msg.id),
            metric_subject=self._metric_subject,
        )

    async def publish_batch(self, msgs: Sequence[OutMsg]) -> list[Exception | None]:
        """Публикует батч async'ом и отдаёт исход каждого сообщения."""
        batch = [
            StreamMsg(
                subject=m.subject,
                payload=m.payload,
                msg_id=self._msg_id(m.id),
                metric_subject=self._metric_subject,
            )
            for m in msgs
        ]
        return await self._sb.publish_stream_batch(batch)


def new_stream_transport(sb: StreamBus, namespace: str = "") -> Transport:
    """Адаптирует JetStream под `Transport`.

    Без namespace `Nats-Msg-Id` — голый id строки: корректно, пока outbox-таблица одна
    (`BIGSERIAL` глобально уникален). Несколько таблиц на одном стриме имеют независимые
    `BIGSERIAL`, поэтому им нужен namespace (обычно имя инстанса): дедуп идёт по `Msg-Id`
    и молча уронил бы второй факт.
    """
    return StreamTransport(sb, namespace)
