"""Соединение инстанса плюс реестр его подписок: graceful `close`, health и логи событий nats-py."""

from __future__ import annotations

import asyncio
import contextlib
import ssl
from collections.abc import Awaitable, Callable
from typing import Any

from nats.aio.client import Client
from nats.errors import SlowConsumerError

from pylibinfra.logkit import Logger, nop

from .config import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_DRAIN_TIMEOUT,
    DEFAULT_INSTANCE,
    Config,
    positive_or,
    redact_url,
)
from .errors import ClosedError, ConnectionClosedError, NatskitError
from .subscription import InflightGroup, Stop, TrackedSubscription

# Запас на сворачивание отменённых обработчиков после исчерпания бюджета drain.
_CANCEL_GRACE = 1.0


class Conn:
    """Соединение с NATS и реестр подписок инстанса.

    `close` дренит подписки, ждёт обработчиков и только потом закрывает сокет —
    обработчики успевают `ack`/`nak` по живому соединению.
    """

    def __init__(
        self,
        nc: Client,
        cfg: Config | None = None,
        log: Logger | None = None,
        *,
        instance: str = DEFAULT_INSTANCE,
    ) -> None:
        cfg = cfg or Config()
        self.instance = instance or DEFAULT_INSTANCE
        self.inflight = InflightGroup()
        self._nc = nc
        self._log = (log or nop()).with_(instance=self.instance)
        self._url = redact_url(cfg.url)
        self._drain_timeout = positive_or(cfg.drain_timeout, DEFAULT_DRAIN_TIMEOUT)
        self._closing = False
        self._subs: set[TrackedSubscription] = set()

    @property
    def raw(self) -> Client:
        """Клиент nats-py под обёрткой — для кода, которому нужен транспорт напрямую."""
        return self._nc

    @property
    def log(self) -> Logger:
        """Логгер инстанса (уже с полем `instance`)."""
        return self._log

    @property
    def closing(self) -> bool:
        """True после начала `close()`: новые подписки отвергаются `ClosedError`."""
        return self._closing

    def status(self) -> str:
        """Текущее состояние соединения: `connected`/`reconnecting`/`draining`/`closed`."""
        if self._nc.is_closed:
            return "closed"
        if self._nc.is_draining:
            return "draining"
        if self._nc.is_reconnecting:
            return "reconnecting"
        if self._nc.is_connected:
            return "connected"
        return "disconnected"

    def healthy(self) -> None:
        """Готовность соединения принимать публикации.

        `ConnectionClosedError` после окончательного закрытия (в т.ч. исчерпания
        reconnect'ов), `NatskitError` со статусом в промежуточных состояниях.
        """
        status = self.status()
        if status == "connected":
            return
        if status == "closed":
            last = self._nc.last_error
            raise ConnectionClosedError(
                f"natskit: connection closed: {last}" if last else "natskit: connection closed"
            )
        raise NatskitError(f"natskit: not connected (status={status})")

    async def register(
        self,
        subject: str,
        start: Callable[[TrackedSubscription], Awaitable[Stop]],
    ) -> TrackedSubscription:
        """Регистрирует подписку атомарно относительно `close`: после его начала — `ClosedError`.

        `start` получает уже созданный handle (в него сыплются обработчики) и
        возвращает транспортную остановку.
        """
        if self._closing:
            raise ClosedError
        sub = TrackedSubscription(self, subject)
        sub.set_stop(await start(sub))
        self._subs.add(sub)
        return sub

    def forget(self, sub: TrackedSubscription) -> None:
        """Убирает подписку из реестра (её уже остановил вызывающий)."""
        self._subs.discard(sub)

    async def close(self, *, timeout: float | None = None) -> None:  # noqa: ASYNC109
        """Graceful-остановка в бюджете `min(NATS_DRAIN_TIMEOUT, timeout)`; повторный вызов — no-op.

        Порядок: drain подписок → ожидание обработчиков → flush публикаций → закрытие
        сокета. По истечении бюджета обработчики отменяются, соединение закрывается
        принудительно, а незавершённые JetStream-сообщения сервер переотправит после AckWait.
        """
        if self._closing:
            return
        self._closing = True
        budget = self._drain_timeout if timeout is None else min(self._drain_timeout, timeout)
        deadline = asyncio.get_running_loop().time() + max(budget, 0.0)
        subs = tuple(self._subs)

        # Дренятся все разом: последовательный drain отдал бы весь бюджет первой подписке.
        await asyncio.gather(*(self._drain_sub(sub, deadline) for sub in subs))
        for sub in subs:
            try:
                await sub.inflight.wait(self._left(deadline))
            except TimeoutError:
                self._log.warn(
                    "natskit: subscription drain",
                    subject=sub.subject,
                    err="drain timeout",
                    inflight=sub.inflight.count,
                )
        try:
            await self.inflight.wait(self._left(deadline))
        except TimeoutError:
            self._log.warn(
                "natskit: drain timeout, handlers still in flight", inflight=self.inflight.count
            )
            self._cancel_handlers()
            with contextlib.suppress(TimeoutError):
                await self.inflight.wait(_CANCEL_GRACE)

        self._subs.clear()
        await self._close_transport(self._left(deadline))

    async def _drain_sub(self, sub: TrackedSubscription, deadline: float) -> None:
        try:
            await sub.request_stop(drain=True, budget=self._left(deadline))
        except Exception as exc:
            self._log.warn("natskit: subscription drain", subject=sub.subject, err=str(exc))

    async def _close_transport(self, left: float) -> None:
        if self._nc.is_closed:
            return
        try:
            await asyncio.wait_for(self._nc.drain(), timeout=max(left, _CANCEL_GRACE))
        except Exception:
            # Flush публикаций не уложился в остаток бюджета либо сорвался: рвём соединение.
            self._log.warn("natskit: drain timeout, closing connection")
            with contextlib.suppress(Exception):
                await self._nc.close()

    def _cancel_handlers(self) -> None:
        for sub in tuple(self._subs):
            sub.cancel_handlers()

    @staticmethod
    def _left(deadline: float) -> float:
        return max(deadline - asyncio.get_running_loop().time(), 0.0)

    def on_closed(self) -> None:
        """Реакция на закрытие сокета: вне `close()` это исчерпание reconnect'ов."""
        if self._closing:
            return
        kv: dict[str, Any] = {"url": self._url}
        if self._nc.last_error is not None:
            kv["err"] = str(self._nc.last_error)
        # Закрытие вне close() — исчерпанные reconnect'ы: подписки мертвы навсегда.
        self._log.error("natskit: connection closed, reconnects exhausted", **kv)


def _auth_options(cfg: Config) -> dict[str, Any]:
    opts: dict[str, Any] = {}
    if cfg.creds_file:
        opts["user_credentials"] = cfg.creds_file
    if cfg.nkey_seed_file:
        opts["nkeys_seed"] = cfg.nkey_seed_file
    if cfg.tls_ca_file:
        opts["tls"] = ssl.create_default_context(cafile=cfg.tls_ca_file)
    return opts


def _callbacks(conn_ref: dict[str, Conn], log: Logger, url: str) -> dict[str, Any]:
    async def error_cb(err: Exception) -> None:
        kv: dict[str, Any] = {"err": str(err)}
        if isinstance(err, SlowConsumerError):
            kv["subject"] = err.subject
            kv["pending"] = err.sub.pending_msgs
        log.error("natskit: async error", **kv)

    async def disconnected_cb() -> None:
        conn = conn_ref.get("conn")
        if conn is not None and conn.closing:
            return
        log.warn("natskit: disconnected", url=url)

    async def reconnected_cb() -> None:
        conn = conn_ref.get("conn")
        connected = conn.raw.connected_url if conn is not None else None
        log.info("natskit: reconnected", url=redact_url(connected.geturl()) if connected else url)

    async def closed_cb() -> None:
        conn = conn_ref.get("conn")
        if conn is not None:
            conn.on_closed()

    return {
        "error_cb": error_cb,
        "disconnected_cb": disconnected_cb,
        "reconnected_cb": reconnected_cb,
        "closed_cb": closed_cb,
    }


async def new_conn(
    cfg: Config, log: Logger | None = None, *, instance: str = DEFAULT_INSTANCE
) -> Conn:
    """Открывает соединение инстанса и вешает логирование событий nats-py.

    Первичное подключение ограничено `NATS_CONNECT_TIMEOUT` на сервер из `NATS_URL`:
    иначе бесконечные reconnect'ы (`NATS_MAX_RECONNECTS=-1`) держали бы старт сервиса.
    """
    base = log or nop()
    bound = base.with_(instance=instance or DEFAULT_INSTANCE)
    shown = redact_url(cfg.url)
    servers = [part.strip() for part in cfg.url.split(",") if part.strip()]
    conn_ref: dict[str, Conn] = {}

    nc = Client()
    # nats-py объявляет таймауты int'ами, но принимает и дробные секунды.
    connect = nc.connect(
        servers=servers,
        connect_timeout=cfg.connect_timeout,  # type: ignore[arg-type]
        reconnect_time_wait=cfg.reconnect_wait,  # type: ignore[arg-type]
        max_reconnect_attempts=cfg.max_reconnects,
        drain_timeout=positive_or(cfg.drain_timeout, DEFAULT_DRAIN_TIMEOUT),  # type: ignore[arg-type]
        **_callbacks(conn_ref, bound, shown),
        **_auth_options(cfg),
    )
    try:
        await asyncio.wait_for(
            connect,
            timeout=positive_or(cfg.connect_timeout, DEFAULT_CONNECT_TIMEOUT) * len(servers),
        )
    except TimeoutError as exc:
        await _abort(nc)
        raise NatskitError(f"natskit: connect {shown}: timeout") from exc
    except Exception as exc:
        await _abort(nc)
        raise NatskitError(f"natskit: connect {shown}: {exc}") from exc

    conn = Conn(nc, cfg, base, instance=instance)
    conn_ref["conn"] = conn
    return conn


async def _abort(nc: Client) -> None:
    with contextlib.suppress(Exception):
        await nc.close()
