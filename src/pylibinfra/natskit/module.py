from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

import nats
from dishka import Provider, Scope, provide

from pylibinfra.logkit import Logger, nop

from .bus import Bus, NatsBus
from .config import Config
from .conn import Conn
from .jetstream import StreamBus, new_stream_bus


# Provider не provide'ит Config (asymmetry note) и не provide'ит Probe — тип probe
# `Callable[..., Awaitable[None]]` коллизит с любым другим health-probe. Caller
# регистрирует probe сам с нужной аннотацией (обычно group="health").
class NatsProvider(Provider):
    scope = Scope.APP

    @provide
    async def conn(self, cfg: Config) -> AsyncIterator[Conn]:
        nc = await nats.connect(
            cfg.url,
            connect_timeout=cfg.connect_timeout,
            reconnect_time_wait=cfg.reconnect_wait,
            max_reconnect_attempts=cfg.max_reconnects,
        )
        try:
            yield Conn(nc)
        finally:
            await nc.drain()

    @provide(provides=Bus)
    def bus(self, conn: Conn, cfg: Config, log: Logger) -> Bus:
        return NatsBus(conn, log, cfg)

    @provide
    async def stream_bus(self, conn: Conn, cfg: Config, log: Logger) -> StreamBus:
        return await new_stream_bus(conn, cfg, log)


def new_config() -> Config:
    return Config()


def probe(conn: Conn) -> Callable[[], Coroutine[Any, Any, None]]:
    async def check() -> None:
        conn.healthy()

    return check


def new_bus(conn: Conn, cfg: Config | None = None, log: Logger | None = None) -> Bus:
    return NatsBus(conn, log or nop(), cfg or Config())
