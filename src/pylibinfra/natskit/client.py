"""Инстанс шины: соединение плюс `Bus` и `StreamBus` поверх него, health-проба инстанса."""

from __future__ import annotations

from pylibinfra import healthkit
from pylibinfra.logkit import Logger

from .bus import Bus, new_bus
from .config import DEFAULT_INSTANCE, Config
from .conn import Conn, new_conn
from .jetstream import StreamBus, new_stream_bus

_PROBE_NAME = "natskit"


class Client:
    """Одна шина процесса: соединение, Core-`Bus` и JetStream-`StreamBus` под общим именем."""

    def __init__(self, name: str, conn: Conn, bus: Bus, stream: StreamBus) -> None:
        self.name = name
        self.conn = conn
        self.bus = bus
        self.stream = stream

    def healthy(self) -> None:
        """Готовность соединения инстанса (см. `Conn.healthy`)."""
        self.conn.healthy()

    async def close(self, *, timeout: float | None = None) -> None:  # noqa: ASYNC109
        """Graceful-остановка инстанса в бюджете `min(NATS_DRAIN_TIMEOUT, timeout)`."""
        await self.conn.close(timeout=timeout)


async def new_client(name: str, cfg: Config, log: Logger | None = None) -> Client:
    """Поднимает инстанс шины: соединение, декларация стримов, `Bus` и `StreamBus`."""
    instance = name or DEFAULT_INSTANCE
    conn = await new_conn(cfg, log, instance=instance)
    try:
        stream = await new_stream_bus(conn, cfg, log)
    except BaseException:
        await conn.close()
        raise
    return Client(instance, conn, new_bus(conn, cfg, log), stream)


def probe(conn: Conn) -> healthkit.Probe:
    """Проба готовности инстанса: `natskit` у дефолтного, `natskit/<instance>` у именованного."""
    name = _PROBE_NAME if conn.instance == DEFAULT_INSTANCE else f"{_PROBE_NAME}/{conn.instance}"

    async def check() -> None:
        conn.healthy()

    return healthkit.new(name, check)
