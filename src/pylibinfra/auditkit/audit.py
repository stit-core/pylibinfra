"""Core-абстракции append-only audit-log: `Entry` и порт `Logger`.

Кит фиксирует кто/когда/что сделал над каким ресурсом (actor_id, action,
resource, metadata), чтобы compliance- и forensics-запросы не приходилось
склеивать из разрозненных логов. Backend (pgstore) отделён от core.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

# Предельные длины полей, попадающих в btree-индексы audit_log: значение длиннее
# не помещается в индексную запись Postgres, и INSERT падает уже в базе.
MAX_ACTOR_ID_LEN = 255
MAX_ACTION_LEN = 255
MAX_RESOURCE_LEN = 255
MAX_RESOURCE_ID_LEN = 255


class AuditError(Exception):
    """База ошибок кита: отказ валидации и сбой записи в хранилище."""


class InvalidEntryError(AuditError, ValueError):
    """Запись не прошла `Entry.validate`: пустое обязательное поле или превышен предел длины."""


@dataclass(frozen=True, slots=True)
class Entry:
    """Одна запись аудита: кто, что сделал над каким ресурсом и когда.

    Пустые строковые поля и пустая `metadata` пишутся в базу как NULL;
    `at is None` подставляется часами базы (`NOW()`), заданный `at` обязан нести
    таймзону.
    """

    action: str
    resource: str
    actor_id: str = ""
    resource_id: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    at: datetime | None = None
    ip: str = ""
    request_id: str = ""

    def validate(self) -> None:
        """Проверяет обязательные поля, предельные длины и `at`; нарушение — `InvalidEntryError`.

        Naive `at` отвергается: драйвер записал бы его в `TIMESTAMPTZ` по часовому
        поясу процесса, и момент события сдвинулся бы на локальное смещение молча.
        """
        if not self.action:
            raise InvalidEntryError("auditkit: invalid entry: action is required")
        if not self.resource:
            raise InvalidEntryError("auditkit: invalid entry: resource is required")
        if self.at is not None and self.at.utcoffset() is None:
            raise InvalidEntryError("auditkit: invalid entry: at must be timezone-aware")
        limits = (
            ("actor_id", self.actor_id, MAX_ACTOR_ID_LEN),
            ("action", self.action, MAX_ACTION_LEN),
            ("resource", self.resource, MAX_RESOURCE_LEN),
            ("resource_id", self.resource_id, MAX_RESOURCE_ID_LEN),
        )
        for name, value, limit in limits:
            if len(value) > limit:
                raise InvalidEntryError(
                    f"auditkit: invalid entry: {name} is longer than {limit} characters"
                )


@runtime_checkable
class Logger(Protocol):
    """Порт записи аудита; реализация в библиотеке — `auditkit.pgstore.Store`."""

    async def log(self, entry: Entry) -> None:
        """Пишет одну запись; невалидная `Entry` — `InvalidEntryError` до похода в базу."""
