"""Опции публикации, общие для `Bus` и `StreamBus`, и единица батч-публикации `StreamMsg`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

MSG_ID_HEADER = "Nats-Msg-Id"
"""Заголовок дедупа JetStream: повтор с тем же id в окне `NATS_JS_DUPLICATES` отбрасывается."""


@dataclass(frozen=True, slots=True)
class PublishSpec:
    """Разобранные опции одной публикации; экспортирован ради фейков шины (`testkit.natstest`)."""

    msg_id: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    metric_subject: str = ""

    def wire_headers(self) -> dict[str, str]:
        """Заголовки сообщения: копия заданных вызывающим плюс `Nats-Msg-Id`.

        Копия, потому что инжект trace-context не должен править словарь продьюсера.
        """
        headers = dict(self.headers)
        if self.msg_id:
            headers[MSG_ID_HEADER] = self.msg_id
        return headers


def new_publish_spec(
    *,
    msg_id: str | None = None,
    headers: Mapping[str, str] | None = None,
    metric_subject: str | None = None,
) -> PublishSpec:
    """Собирает `PublishSpec` из keyword-опций публикации (None — опция не задана)."""
    return PublishSpec(
        msg_id=msg_id or "",
        headers=dict(headers) if headers else {},
        metric_subject=metric_subject or "",
    )


@dataclass(frozen=True, slots=True)
class StreamMsg:
    """Единица батч-публикации в JetStream.

    `msg_id` ставит `Nats-Msg-Id` (дедуп стримом), `metric_subject` — значение label
    `subject` publish-метрик; пусто — label отсутствует.
    """

    subject: str
    payload: bytes
    msg_id: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    metric_subject: str = ""

    def spec(self) -> PublishSpec:
        """Опции публикации этого сообщения."""
        return PublishSpec(
            msg_id=self.msg_id, headers=self.headers, metric_subject=self.metric_subject
        )
