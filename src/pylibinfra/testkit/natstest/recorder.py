"""Recorder — in-memory `natskit.Bus` и `natskit.StreamBus` с синхронной доставкой."""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pylibinfra.natskit import (
    Bus,
    Handler,
    StreamBus,
    StreamMsg,
    Subscription,
    message_context,
    new_publish_spec,
    subject_matches,
)


@dataclass(frozen=True, slots=True)
class Message:
    """Записанная публикация — то же, что уехало бы в реальную шину."""

    subject: str
    payload: bytes
    headers: dict[str, str] = field(default_factory=dict)
    msg_id: str = ""
    metric_subject: str = ""
    jetstream: bool = False


@dataclass(slots=True)
class _Sub:
    id: int
    filter: str
    queue: str
    jetstream: bool
    handler: Handler


class _Subscription:
    """Handle подписки фейка: доставка синхронна, поэтому ждать в `drain` нечего."""

    def __init__(self, recorder: Recorder, sub_id: int) -> None:
        self._recorder = recorder
        self._id = sub_id

    async def unsubscribe(self) -> None:
        """Снимает подписку."""
        self._recorder.remove(self._id)

    async def drain(self, *, timeout: float | None = None) -> None:  # noqa: ASYNC109
        """Снимает подписку; незавершённых обработчиков у синхронной доставки нет."""
        self._recorder.remove(self._id)


class Recorder:
    """Фейк шины: копит публикации и синхронно отдаёт их подписчикам.

    После `publish*` обработчики уже отработали — ждать нечего; их ошибки видны в
    `handler_errors()` и на публикацию не влияют (как в реальной шине).
    """

    def __init__(self) -> None:
        self._published: list[Message] = []
        self._subs: list[_Sub] = []
        self._handler_errors: list[BaseException] = []
        self._publish_error: Exception | None = None
        self._ids = itertools.count(1)

    def set_publish_error(self, err: Exception | None) -> None:
        """Включает ветку «шина недоступна»: все публикации отдают `err` (None — снова успех)."""
        self._publish_error = err

    def reset(self) -> None:
        """Очищает записи публикаций и ошибок обработчиков; подписки остаются."""
        self._published.clear()
        self._handler_errors.clear()

    def published(self) -> list[Message]:
        """Все публикации в порядке отправки."""
        return list(self._published)

    def published_on(self, filter_: str) -> list[Message]:
        """Публикации в subject'ы, накрытые фильтром (wildcard'ы NATS работают)."""
        return [m for m in self._published if subject_matches(filter_, m.subject)]

    def handler_errors(self) -> list[BaseException]:
        """Ошибки, которые вернули обработчики при доставке."""
        return list(self._handler_errors)

    def subscriptions(self) -> list[str]:
        """Фильтры зарегистрированных подписок в порядке регистрации."""
        return [s.filter for s in self._subs]

    async def publish(
        self,
        subject: str,
        payload: bytes,
        *,
        msg_id: str | None = None,
        headers: Mapping[str, str] | None = None,
        metric_subject: str | None = None,
    ) -> None:
        """Записывает Core-публикацию и синхронно доставляет её Core-подписчикам."""
        await self._publish(
            subject, payload, jetstream=False, msg_id=msg_id, headers=headers, metric=metric_subject
        )

    async def publish_stream(
        self,
        subject: str,
        payload: bytes,
        *,
        msg_id: str | None = None,
        headers: Mapping[str, str] | None = None,
        metric_subject: str | None = None,
    ) -> None:
        """Записывает stream-публикацию и доставляет её stream-подписчикам."""
        await self._publish(
            subject, payload, jetstream=True, msg_id=msg_id, headers=headers, metric=metric_subject
        )

    async def publish_stream_batch(self, msgs: Sequence[StreamMsg]) -> list[Exception | None]:
        """Публикует каждое сообщение батча; `results[i]` — исход i-го."""
        results: list[Exception | None] = []
        for msg in msgs:
            try:
                await self._publish(
                    msg.subject,
                    msg.payload,
                    jetstream=True,
                    msg_id=msg.msg_id,
                    headers=msg.headers,
                    metric=msg.metric_subject,
                )
            except Exception as exc:
                results.append(exc)
                continue
            results.append(None)
        return results

    async def subscribe(self, subject: str, handler: Handler) -> Subscription:
        """Регистрирует fan-out подписку."""
        return self._subscribe(subject, "", jetstream=False, handler=handler)

    async def subscribe_queue(self, subject: str, queue: str, handler: Handler) -> Subscription:
        """Регистрирует подписку queue-группы: сообщение получает один её участник."""
        return self._subscribe(subject, queue, jetstream=False, handler=handler)

    async def subscribe_stream(
        self, stream: str, subject: str, durable: str, handler: Handler
    ) -> Subscription:
        """Регистрирует stream-подписку; `durable` играет роль queue-группы."""
        return self._subscribe(subject, durable, jetstream=True, handler=handler)

    async def deliver(
        self,
        subject: str,
        payload: bytes,
        *,
        msg_id: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Отдаёт сообщение подписчикам, как будто оно пришло из шины (публикацией не считается)."""
        spec = new_publish_spec(msg_id=msg_id, headers=headers)
        await self._deliver(subject, payload, spec.wire_headers(), jetstream=None)

    def remove(self, sub_id: int) -> None:
        """Убирает подписку по идентификатору (зовёт handle подписки)."""
        self._subs = [s for s in self._subs if s.id != sub_id]

    async def _publish(
        self,
        subject: str,
        payload: bytes,
        *,
        jetstream: bool,
        msg_id: str | None,
        headers: Mapping[str, str] | None,
        metric: str | None,
    ) -> None:
        spec = new_publish_spec(msg_id=msg_id, headers=headers, metric_subject=metric)
        if self._publish_error is not None:
            raise self._publish_error
        wire = spec.wire_headers()
        self._published.append(
            Message(
                subject=subject,
                payload=bytes(payload),
                headers=wire,
                msg_id=spec.msg_id,
                metric_subject=spec.metric_subject,
                jetstream=jetstream,
            )
        )
        await self._deliver(subject, payload, wire, jetstream=jetstream)

    async def _deliver(
        self, subject: str, payload: bytes, headers: dict[str, str], *, jetstream: bool | None
    ) -> None:
        seen_queues: set[str] = set()
        targets: list[_Sub] = []
        for sub in self._subs:
            if jetstream is not None and sub.jetstream != jetstream:
                continue
            if not subject_matches(sub.filter, subject):
                continue
            # Queue-группа: сообщение получает один подписчик группы, как в NATS.
            if sub.queue:
                if sub.queue in seen_queues:
                    continue
                seen_queues.add(sub.queue)
            targets.append(sub)

        for sub in targets:
            try:
                with message_context(subject, headers):
                    await sub.handler(payload)
            except Exception as exc:
                self._handler_errors.append(exc)

    def _subscribe(
        self, subject: str, queue: str, *, jetstream: bool, handler: Handler
    ) -> Subscription:
        sub = _Sub(
            id=next(self._ids),
            filter=subject,
            queue=queue,
            jetstream=jetstream,
            handler=handler,
        )
        self._subs.append(sub)
        return _Subscription(self, sub.id)


def new() -> Recorder:
    """Создаёт пустой Recorder."""
    return Recorder()


if TYPE_CHECKING:
    # Расхождение фейка с портами кита обязано падать на mypy, а не в тесте консьюмера.
    _bus: Bus = Recorder()
    _stream_bus: StreamBus = Recorder()
