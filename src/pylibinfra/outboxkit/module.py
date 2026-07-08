import asyncio
from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide

from pylibinfra.logkit import Logger
from pylibinfra.natskit import StreamBus

from .bus import Bus, Publisher
from .config import Config
from .metrics import BacklogReporter, register_backlog_gauges
from .outbox import Repository
from .transport import Transport, new_stream_transport
from .worker import Worker


# Provider не provide'ит Config (asymmetry note, см. telemetry/module.py) — caller
# регистрирует Config сам. Дешёвый mode: new_config() ниже. Repository не
# provide'ится здесь: его даёт pgstore.OutboxPgstoreProvider или собственный
# store-адаптер. Transport строится поверх natskit.StreamBus (JetStream). Gauge'и
# outbox_lag_seconds/outbox_pending_count — только если Repository реализует
# BacklogReporter.
class OutboxProvider(Provider):
    scope = Scope.APP

    @provide(provides=Bus)
    def bus(self, repo: Repository) -> Bus:
        return Bus(repo)

    @provide
    def publisher(self, bus: Bus) -> Publisher:
        return bus

    @provide
    def transport(self, stream_bus: StreamBus) -> Transport:
        return new_stream_transport(stream_bus)

    @provide
    async def worker(
        self, repo: Repository, tr: Transport, cfg: Config, log: Logger
    ) -> AsyncIterator[Worker]:
        w = Worker(repo, tr, cfg, log)
        if isinstance(repo, BacklogReporter):
            register_backlog_gauges(repo)
        task = asyncio.create_task(w.run_loop(), name="outboxkit.publisher")
        try:
            yield w
        finally:
            task.cancel()
            # Дожидаемся фактической отмены: иначе задача переживёт lifecycle-выход
            # и asyncio пожалуется на "task was destroyed but it is pending".
            await asyncio.gather(task, return_exceptions=True)


def new_config() -> Config:
    return Config()
