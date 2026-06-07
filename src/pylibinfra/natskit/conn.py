"""Conn — обёртка над nats Client. new_conn навешивает reconnect-колбэки,
логирующие разрыв/восстановление: при max_reconnects=-1 (дефолт) бот переживает
длительную недоступность NATS, а durable JetStream-консьюмеры восстанавливаются
автоматически (подписка-объект живёт через reconnect — явная переподписка не нужна).
"""

from __future__ import annotations

import nats
from nats.aio.client import Client

from pylibinfra.logkit import Logger, nop

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


def _callbacks(log: Logger) -> dict[str, object]:
    async def disconnected_cb() -> None:
        log.warn("natskit: disconnected from NATS")

    async def reconnected_cb() -> None:
        log.info("natskit: reconnected to NATS")

    async def error_cb(err: Exception) -> None:
        log.error("natskit: connection error", err=str(err))

    async def closed_cb() -> None:
        log.warn("natskit: connection closed (reconnection stopped)")

    return {
        "disconnected_cb": disconnected_cb,
        "reconnected_cb": reconnected_cb,
        "error_cb": error_cb,
        "closed_cb": closed_cb,
    }


async def new_conn(cfg: Config, log: Logger | None = None) -> Conn:
    nc = await nats.connect(
        cfg.url,
        connect_timeout=cfg.connect_timeout,
        reconnect_time_wait=cfg.reconnect_wait,
        max_reconnect_attempts=cfg.max_reconnects,
        **_callbacks(log or nop()),
    )
    return Conn(nc)
