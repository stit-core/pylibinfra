"""dishka-проводка outbox'а: single-instance и инстансы на своих таблицах."""

from __future__ import annotations

from typing import Any

from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import logkit
from pylibinfra.natskit import StreamBus
from pylibinfra.outboxkit import (
    Bus,
    Config,
    Outbox,
    OutboxInstanceProvider,
    OutboxProvider,
    Publisher,
    Repository,
    RepositoryFactory,
    Transport,
    Worker,
)
from pylibinfra.pgkit import Transactor
from pylibinfra.testkit import natstest, pgtest
from pylibinfra.testkit.logtest import Capture

from .fakes import FakeRepo, message


class _AppProvider(Provider):
    scope = Scope.APP

    def __init__(self, repo: FakeRepo, recorder: natstest.Recorder) -> None:
        super().__init__()
        self._repo = repo
        self._recorder = recorder

    @provide
    def config(self) -> Config:
        return Config(batch_size=10)

    @provide
    def logger(self) -> logkit.Logger:
        return Capture()

    @provide
    def transactor(self) -> Transactor:
        return pgtest.Identity()

    @provide
    def stream_bus(self) -> StreamBus:
        return self._recorder

    @provide
    def repository(self) -> Repository:
        return self._repo

    @provide
    def factory(self) -> RepositoryFactory:
        async def build(table: str) -> Repository:
            self._repo.table = table  # type: ignore[attr-defined]
            return self._repo

        return build


async def test_single_instance_graph_publishes_and_drains() -> None:
    repo = FakeRepo([message(1)])
    recorder = natstest.new()
    container = make_async_container(OutboxProvider(), _AppProvider(repo, recorder))
    try:
        outbox = await container.get(Outbox)
        publisher = await container.get(Publisher)
        worker = await container.get(Worker)
        transport = await container.get(Transport)
        bus = await container.get(Bus)

        assert outbox.name == "outbox"
        assert bus is publisher
        assert isinstance(transport, object)

        await publisher.publish("order.created", {"id": 7})
        await worker.run()

        # Дефолтный инстанс публикует с голым id строки.
        assert [m.msg_id for m in recorder.published()] == ["1", "100"]
        assert {m.metric_subject for m in recorder.published()} == {"outbox"}
    finally:
        await container.close()


async def test_named_instance_lives_in_its_own_component() -> None:
    repo = FakeRepo([message(1)])
    recorder = natstest.new()
    container = make_async_container(
        OutboxInstanceProvider("orders", "outbox_orders"),
        _AppProvider(repo, recorder),
    )
    try:
        outbox = await container.get(Outbox, component="orders")
        worker = await container.get(Worker, component="orders")

        await worker.run()

        assert outbox.name == "orders"
        assert getattr(repo, "table", "") == "outbox_orders"
        # Msg-Id намспейсится именем инстанса: таблицы делят один стрим.
        assert [m.msg_id for m in recorder.published()] == ["orders:1"]
        assert [job.name for job in outbox.jobs()] == ["outboxkit.publisher.orders"]
    finally:
        await container.close()


async def test_named_instance_can_override_config() -> None:
    repo = FakeRepo()
    recorder = natstest.new()
    ordered = Config(publish_mode="ordered", interval=5.0)  # type: ignore[arg-type]
    container = make_async_container(
        OutboxInstanceProvider("leads", "outbox_leads", cfg=ordered),
        _AppProvider(repo, recorder),
    )
    try:
        outbox = await container.get(Outbox, component="leads")

        assert outbox.job().interval == 5.0
    finally:
        await container.close()


def _unused(*_: Any) -> None:  # pragma: no cover - помощник для читаемости импортов
    return None
