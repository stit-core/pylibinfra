from __future__ import annotations

import nats
from nats.aio.client import Client

from .config import Config


class Conn:
    def __init__(self, nc: Client) -> None:
        self._nc = nc

    @property
    def raw(self) -> Client:
        return self._nc

    def healthy(self) -> None:
        if not self._nc.is_connected:
            raise RuntimeError("natskit: not connected")


async def new_conn(cfg: Config) -> Conn:
    nc = await nats.connect(
        cfg.url,
        connect_timeout=cfg.connect_timeout,
        reconnect_time_wait=cfg.reconnect_wait,
        max_reconnect_attempts=cfg.max_reconnects,
    )
    return Conn(nc)
