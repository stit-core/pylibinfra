import asyncio
from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide

from pylibinfra.logkit import Logger
from pylibinfra.natskit import Bus as NatsBus

from .bus import Bus, Publisher
from .config import Config
from .metrics import register_lag_gauge
from .outbox import LagReporter, Store
from .worker import Worker


# Provider не provide'ит Config (asymmetry note, см. telemetry/module.py) — caller
# регистрирует Config сам. Дешёвый mode: new_config() ниже. Store не provide'ится
# здесь: его даёт pgstore.OutboxPgstoreProvider или собственный store-адаптер.
# outbox_lag_seconds эмитится только если Store также реализует LagReporter.
class OutboxProvider(Provider):
    scope = Scope.APP

    @provide(provides=Bus)
    def bus(self, store: Store) -> Bus:
        return Bus(store)

    @provide
    def publisher(self, bus: Bus) -> Publisher:
        return bus

    @provide
    async def worker(
        self, store: Store, nats_bus: NatsBus, cfg: Config, log: Logger
    ) -> AsyncIterator[Worker]:
        w = Worker(store, nats_bus, cfg, log)
        if isinstance(store, LagReporter):
            register_lag_gauge(store)
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
