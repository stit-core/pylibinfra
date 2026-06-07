"""Core-абстракции append-only audit-log: Entry + порты Recorder и Store.

Кит фиксирует кто/когда/что сделал над каким ресурсом (actor_id, action,
resource, metadata), чтобы compliance- и forensics-запросы не приходилось
склеивать из разрозненных логов. Backend (pgstore) отделён от core: core знает
только про порт Store, конкретная запись — в подпакете.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Entry:
    action: str
    resource: str
    actor_id: str = ""
    resource_id: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    # Пустой at трактуется backend'ом как "сейчас" (см. PgStore.record).
    at: datetime | None = None
    ip: str = ""
    request_id: str = ""


@runtime_checkable
class Store(Protocol):
    """Backend-порт записи Entry; реализуется в auditkit.pgstore."""

    async def record(self, entry: Entry) -> None: ...


@runtime_checkable
class Recorder(Protocol):
    """Публичный порт, который консьюмер инжектит для записи аудита."""

    async def record(self, entry: Entry) -> None: ...


class StoreRecorder:
    """Recorder поверх произвольного Store — точка для будущих cross-cutting
    обвязок (батчинг, спаны) без правки backend'а."""

    def __init__(self, store: Store) -> None:
        self._store = store

    async def record(self, entry: Entry) -> None:
        await self._store.record(entry)
