"""dishka-проводка: `HttpProvider` отдаёт `Server` и `FastAPI`, `setup` вносит контейнер в app."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI

from pylibinfra import healthkit
from pylibinfra.logkit import Logger

from .auth import AuthExtractor
from .config import Config
from .idempotency import IdempotencyStore
from .server import Middlewares, SecuritySchemes, Server

# Опциональные зависимости выражены типами-обёртками: dishka не инжектит по `X | None`, но
# последний зарегистрированный провайдер побеждает — consumer перекрывает дефолт своим.
OptionalStore = IdempotencyStore | None
OptionalAuth = AuthExtractor | None
OptionalSecuritySchemes = SecuritySchemes | None


class HttpProvider(Provider):
    """Provide'ит `Server` (порт занимается на старте) и его `FastAPI`.

    Config не provide'ит — его регистрирует caller (`new_config` или свой App-Provider).
    Пробы берутся из `healthkit.Probes`, middleware консьюмера — из `httpkit.Middlewares`.
    """

    scope = Scope.APP

    def __init__(self, *, autostart: bool = True) -> None:
        """`autostart=False` собирает `Server` без занятия порта: сервит его сам консьюмер."""
        super().__init__()
        self.autostart = autostart

    @provide
    def default_store(self) -> OptionalStore:
        """Без store идемпотентность выключена."""
        return None

    @provide
    def default_auth(self) -> OptionalAuth:
        """Без extractor'а auth-гейт не подключается."""
        return None

    @provide
    def default_probes(self) -> healthkit.Probes:
        """Пустой набор проб: `/ready` отвечает `ok` без списка."""
        return ()

    @provide
    def default_middlewares(self) -> Middlewares:
        """Пустой хвост цепочки."""
        return ()

    @provide
    def default_security_schemes(self) -> OptionalSecuritySchemes:
        """Дефолтных схем безопасности OpenAPI кит не выставляет."""
        return None

    @provide
    async def server(
        self,
        cfg: Config,
        log: Logger,
        store: OptionalStore,
        auth: OptionalAuth,
        probes: healthkit.Probes,
        middlewares: Middlewares,
        security_schemes: OptionalSecuritySchemes,
    ) -> AsyncIterator[Server]:
        """Занимает порт синхронно (занятый — `ListenError` и провал старта) и сервит фоном."""
        srv = Server(
            cfg,
            log,
            store=store,
            auth=auth,
            probes=probes,
            middlewares=middlewares,
            security_schemes=security_schemes,
            autostart=self.autostart,
        )
        if not srv.autostart:
            yield srv
            return

        srv.listen()
        log.info("http server listening", addr=srv.addr())
        task = asyncio.create_task(srv.serve())
        task.add_done_callback(lambda t: _report(t, log))
        # даём serve стартовать: иначе close() контейнера пришёл бы раньше первого шага задачи
        await asyncio.sleep(0)
        try:
            yield srv
        finally:
            await srv.stop()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @provide
    def app(self, server: Server) -> FastAPI:
        """FastAPI-приложение сервера: на нём регистрируются роуты консьюмера."""
        return server.app


def _report(task: asyncio.Task[None], log: Logger) -> None:
    """Упавший listener — не «работающий» сервис: терминальная ошибка обязана попасть в лог."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("http server stopped", err=repr(exc))


def setup(app: FastAPI, container: object) -> None:
    """Прокидывает dishka-контейнер в FastAPI, чтобы роуты принимали `FromDishka[...]`."""
    setup_dishka(container, app)  # type: ignore[arg-type]
