"""dishka-проводка: single-instance `OutboxProvider` и multi-instance `OutboxInstanceProvider`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from dishka import DEFAULT_COMPONENT, FromComponent, Provider, Scope, provide

from pylibinfra.logkit import Logger
from pylibinfra.natskit import StreamBus
from pylibinfra.pgkit import Transactor

from .bus import Bus, Publisher
from .config import DEFAULT_INSTANCE, Config
from .instance import Outbox
from .outbox import Repository, RepositoryFactory
from .transport import Transport, new_stream_transport
from .worker import Worker

_Default = FromComponent(DEFAULT_COMPONENT)


class OutboxProvider(Provider):
    """Single-instance путь: `Repository` даёт store-кит, `Config` — консьюмер (asymmetry note).

    Gauge'и backlog'а появляются, если `Repository` реализует `BacklogReporter`.
    Job'ы кит не provide'ит — консьюмер складывает `Outbox.jobs()` в `timerkit.Jobs`.
    """

    scope = Scope.APP

    def __init__(self, name: str = DEFAULT_INSTANCE, *, component: str | None = None) -> None:
        super().__init__(component=component)
        self.name = name

    @provide
    async def outbox(
        self,
        repo: Annotated[Repository, _Default],
        sb: Annotated[StreamBus, _Default],
        tx: Annotated[Transactor, _Default],
        cfg: Config,
        log: Annotated[Logger, _Default],
    ) -> AsyncIterator[Outbox]:
        """Инстанс на всё время жизни контейнера; наблюдение backlog'а снимается на выходе."""
        namespace = "" if self.name == DEFAULT_INSTANCE else self.name
        outbox = Outbox(self.name, repo, new_stream_transport(sb, namespace), tx, cfg, log)
        registration = outbox.register_backlog_gauges()
        try:
            yield outbox
        finally:
            if registration is not None:
                registration.unregister()

    @provide
    def bus(self, outbox: Outbox) -> Bus:
        """Insert-сторона инстанса (конкретный тип)."""
        return outbox.bus()

    @provide
    def publisher(self, outbox: Outbox) -> Publisher:
        """Insert-сторона инстанса для доменного кода."""
        return outbox.publisher()

    @provide
    def worker(self, outbox: Outbox) -> Worker:
        """Drain-сторона инстанса; гоняет её `timerkit` через `Outbox.job()`."""
        return outbox.worker()

    @provide
    def transport(self, sb: Annotated[StreamBus, _Default]) -> Transport:
        """JetStream-транспорт инстанса (тот же, что внутри `Outbox`)."""
        return new_stream_transport(sb, "" if self.name == DEFAULT_INSTANCE else self.name)


class OutboxInstanceProvider(Provider):
    """Multi-instance путь: инстанс на своей таблице поверх `RepositoryFactory`.

    Инстанс живёт в своём dishka-компоненте (по умолчанию — его имя), а `Nats-Msg-Id`
    намспейсится этим именем: независимые `BIGSERIAL` таблиц иначе столкнулись бы в
    дедупе стрима. `cfg` перекрывает общий `Config` (например ordered-режим у одного домена).
    """

    scope = Scope.APP

    def __init__(
        self, name: str, table: str, *, cfg: Config | None = None, component: str | None = None
    ) -> None:
        super().__init__(component=name if component is None else component)
        self.name = name
        self.table = table
        self.cfg = cfg

    @provide
    async def outbox(
        self,
        factory: Annotated[RepositoryFactory, _Default],
        sb: Annotated[StreamBus, _Default],
        tx: Annotated[Transactor, _Default],
        cfg: Annotated[Config, _Default],
        log: Annotated[Logger, _Default],
    ) -> AsyncIterator[Outbox]:
        """Инстанс на таблице `table`; наблюдение backlog'а снимается на выходе."""
        repo = await factory(self.table)
        outbox = Outbox(
            self.name, repo, new_stream_transport(sb, self.name), tx, self.cfg or cfg, log
        )
        registration = outbox.register_backlog_gauges()
        try:
            yield outbox
        finally:
            if registration is not None:
                registration.unregister()

    @provide
    def bus(self, outbox: Outbox) -> Bus:
        """Insert-сторона инстанса (конкретный тип)."""
        return outbox.bus()

    @provide
    def publisher(self, outbox: Outbox) -> Publisher:
        """Insert-сторона инстанса для доменного кода."""
        return outbox.publisher()

    @provide
    def worker(self, outbox: Outbox) -> Worker:
        """Drain-сторона инстанса."""
        return outbox.worker()
