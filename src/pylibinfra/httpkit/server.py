"""Server: собирает FastAPI app, навешивает middleware-цепочку и error-handlers,
инструментирует OTel'ом, держит uvicorn.Server. start() поднимает сервер,
stop() корректно гасит с учётом shutdown_timeout.
"""

from __future__ import annotations

import asyncio

import uvicorn
from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from pylibinfra.logkit import Logger, nop

from .auth import AuthExtractor, AuthMiddleware
from .config import Config
from .cors import apply_cors
from .error import register_error_handlers
from .health import Probe, register_health
from .idempotency import IdempotencyMiddleware, IdempotencyStore
from .lifespan import Lifespan
from .maxbody import MaxBodyMiddleware
from .metrics import HTTPInstruments, new_instruments
from .middleware import (
    AccessLogMiddleware,
    MetricsMiddleware,
    RecoveryMiddleware,
    RequestIDMiddleware,
)
from .ratelimit import RateLimitMiddleware
from .security import SecurityHeadersMiddleware


def _addr_host_port(addr: str) -> tuple[str, int]:
    host, _, port = addr.rpartition(":")
    # "0.0.0.0" по умолчанию зеркалит Go-дефолт ":8080" (bind-all) — это сервер.
    return (host or "0.0.0.0", int(port) if port else 8080)  # noqa: S104


def build_app(
    cfg: Config,
    log: Logger,
    *,
    instruments: HTTPInstruments | None = None,
    store: IdempotencyStore | None = None,
    auth: AuthExtractor | None = None,
    probes: tuple[Probe, ...] = (),
    lifespan: Lifespan | None = None,
) -> FastAPI:
    instr = instruments if instruments is not None else new_instruments()
    app = FastAPI(title=cfg.api_title, version=cfg.api_version, lifespan=lifespan)

    register_error_handlers(app)
    register_health(app, *probes)

    # add_middleware кладёт наружу: последний добавленный — самый внешний. Чтобы
    # порядок выполнения совпал с golibinfra (CORS снаружи, Idempotency внутри),
    # добавляем от внутреннего к внешнему.
    if store is not None:
        app.add_middleware(
            IdempotencyMiddleware, cfg=cfg.idempotency, store=store, instruments=instr
        )
    app.add_middleware(MetricsMiddleware, instruments=instr)
    app.add_middleware(AccessLogMiddleware, log=log)
    app.add_middleware(SecurityHeadersMiddleware, cfg=cfg.security)
    app.add_middleware(RecoveryMiddleware, log=log, instruments=instr)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(MaxBodyMiddleware, cfg=cfg.max_body, instruments=instr)
    app.add_middleware(RateLimitMiddleware, cfg=cfg.rate_limit)
    if auth is not None:
        app.add_middleware(AuthMiddleware, extractor=auth)
    apply_cors(app, cfg.cors)

    # FastAPIInstrumentor даёт серверные спаны/трейс-context. instrument_app
    # идемпотентен по флагу _is_instrumented_by_opentelemetry на app.
    FastAPIInstrumentor.instrument_app(app)

    return app


class Server:
    def __init__(
        self,
        cfg: Config,
        log: Logger | None = None,
        *,
        store: IdempotencyStore | None = None,
        auth: AuthExtractor | None = None,
        probes: tuple[Probe, ...] = (),
        instruments: HTTPInstruments | None = None,
        lifespan: Lifespan | None = None,
        autostart: bool = True,
    ) -> None:
        self._cfg = cfg
        self._log = log if log is not None else nop()
        # autostart=False оставляет Server лишь оболочкой над .app (consumer сам
        # решает, как сервить) — start() становится no-op. Webhook-runner так не
        # делает: он дорегистрирует роуты в .app, затем зовёт start() штатно.
        self._autostart = autostart
        self.app = build_app(
            cfg,
            self._log,
            instruments=instruments,
            store=store,
            auth=auth,
            probes=probes,
            lifespan=lifespan,
        )
        host, port = _addr_host_port(cfg.addr)
        self._uvicorn = uvicorn.Server(
            uvicorn.Config(
                self.app,
                host=host,
                port=port,
                timeout_keep_alive=int(cfg.idle_timeout),
                timeout_graceful_shutdown=int(cfg.shutdown_timeout),
                log_config=None,
                access_log=False,
            )
        )
        self._serve_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if not self._autostart:
            return
        self._serve_task = asyncio.create_task(self._uvicorn.serve())

    async def stop(self) -> None:
        self._uvicorn.should_exit = True
        if self._serve_task is not None:
            try:
                await asyncio.wait_for(self._serve_task, timeout=self._cfg.shutdown_timeout)
            except TimeoutError:
                self._serve_task.cancel()
