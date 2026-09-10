"""Server: сборка FastAPI-приложения с цепочкой кита, синхронный listen и graceful stop."""

from __future__ import annotations

import asyncio
import math
import socket
from collections.abc import Sequence
from typing import Any

import uvicorn
from fastapi import FastAPI
from starlette.middleware import Middleware

from pylibinfra import healthkit
from pylibinfra.logkit import Logger, nop

from .auth import AuthExtractor, AuthMiddleware
from .config import Config
from .cors import CORSMiddleware
from .error import HttpkitError, register_error_handlers
from .health import HEALTH_PATH, READY_PATH, register_health
from .idempotency import IdempotencyMiddleware, IdempotencyStore
from .lifespan import Lifespan
from .maxbody import MaxBodyMiddleware
from .metrics import Instruments, MetricsMiddleware, instruments
from .middleware import (
    AccessLogMiddleware,
    OTelMiddleware,
    RecoveryMiddleware,
    RequestIDMiddleware,
)
from .ratelimit import RateLimitMiddleware
from .security import SecurityHeadersMiddleware

DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")

Middlewares = tuple[Middleware, ...]
"""DI-ключ: ASGI-middleware консьюмера; встают в хвост цепочки кита, ближе всего к обработчику."""

SecuritySchemes = dict[str, dict[str, Any]]
"""`components.securitySchemes` OpenAPI; дефолтных схем кит не выставляет."""


class ListenError(HttpkitError):
    """Порт занят или адрес невалиден — сервер не стартует."""


def split_addr(addr: str) -> tuple[str, int]:
    """Разбирает `host:port` (`:8080`, `127.0.0.1:0`, `[::]:8080`); пустой host — все интерфейсы."""
    host, _, port = addr.rpartition(":")
    host = host.strip("[]")
    try:
        number = int(port) if port else 8080
    except ValueError as exc:
        raise ListenError(f"httpkit: invalid addr {addr!r}") from exc
    return (host, number)


def bind_socket(host: str, port: int) -> socket.socket:
    """Создаёт и биндит слушающий сокет; пустой host — dual-stack IPv6+IPv4, как `net.Listen` в Go.

    Без IPv6 в системе bind-all уходит на IPv4. Ошибка bind — `OSError` вызывающему.
    """
    if host == "":
        if socket.has_ipv6:
            sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
                sock.bind(("::", port))
                return sock
            except OSError:
                sock.close()
        host = "0.0.0.0"  # noqa: S104 - bind-all: IPv6 в системе нет
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError:
        sock.close()
        raise
    return sock


def _install_security_schemes(app: FastAPI, schemes: SecuritySchemes) -> None:
    """Дописывает `components.securitySchemes` в сгенерированную FastAPI схему OpenAPI."""
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        schema = generate()
        components = schema.setdefault("components", {})
        components.setdefault("securitySchemes", {}).update(schemes)
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


def build_app(
    cfg: Config,
    log: Logger | None = None,
    *,
    store: IdempotencyStore | None = None,
    auth: AuthExtractor | None = None,
    probes: healthkit.Probes = (),
    middlewares: Sequence[Middleware] = (),
    security_schemes: SecuritySchemes | None = None,
    lifespan: Lifespan | None = None,
    instr: Instruments | None = None,
) -> FastAPI:
    """Собирает FastAPI с цепочкой кита, обработчиками ошибок и `/health` + `/ready`.

    Порядок цепочки снаружи внутрь: RequestID, OTel, AccessLog, Metrics, Recovery,
    SecurityHeaders, CORS, RateLimit, MaxBody, Idempotency, Auth, затем `middlewares`
    консьюмера — они ближе всего к обработчику.
    """
    logger = log if log is not None else nop()
    instruments_ = instr if instr is not None else instruments()
    app = FastAPI(
        title=cfg.api_title,
        version=cfg.api_version,
        lifespan=lifespan,
        docs_url="/docs" if cfg.docs_enabled else None,
        redoc_url="/redoc" if cfg.docs_enabled else None,
        openapi_url="/openapi.json" if cfg.docs_enabled else None,
    )
    if security_schemes:
        _install_security_schemes(app, security_schemes)

    register_error_handlers(app, logger)
    register_health(app, logger, cfg.ready_probe_timeout, probes)

    chain: list[Middleware] = [
        Middleware(RequestIDMiddleware),
        Middleware(OTelMiddleware, quiet_paths=cfg.quiet_paths),
        Middleware(AccessLogMiddleware, log=logger, quiet_paths=cfg.quiet_paths),
        Middleware(MetricsMiddleware, instr=instruments_),
        Middleware(RecoveryMiddleware, log=logger, instr=instruments_),
        Middleware(SecurityHeadersMiddleware, cfg=cfg.security),
    ]
    if cfg.cors.origins:
        chain.append(Middleware(CORSMiddleware, cfg=cfg.cors))
    if cfg.rate_limit.rps > 0:
        chain.append(Middleware(RateLimitMiddleware, cfg=cfg.rate_limit, instr=instruments_))
    if cfg.max_body.max_bytes > 0:
        chain.append(Middleware(MaxBodyMiddleware, cfg=cfg.max_body, instr=instruments_))
    if store is not None:
        chain.append(
            Middleware(
                IdempotencyMiddleware,
                cfg=cfg.idempotency,
                store=store,
                instr=instruments_,
                extractor=auth,
                log=logger,
            )
        )
    if auth is not None:
        chain.append(
            Middleware(
                AuthMiddleware,
                extractor=auth,
                log=logger,
                public_paths=(HEALTH_PATH, READY_PATH, *DOCS_PATHS, *cfg.quiet_paths),
            )
        )
    chain.extend(middlewares)

    # add_middleware кладёт наружу: чтобы порядок выполнения совпал с цепочкой, добавляем с хвоста.
    for entry in reversed(chain):
        app.add_middleware(entry.cls, *entry.args, **entry.kwargs)
    return app


class Server:
    """FastAPI-приложение кита плюс uvicorn: `listen` занимает порт, `serve` обслуживает."""

    def __init__(
        self,
        cfg: Config,
        log: Logger | None = None,
        *,
        store: IdempotencyStore | None = None,
        auth: AuthExtractor | None = None,
        probes: healthkit.Probes = (),
        middlewares: Sequence[Middleware] = (),
        security_schemes: SecuritySchemes | None = None,
        lifespan: Lifespan | None = None,
        instr: Instruments | None = None,
        autostart: bool = True,
    ) -> None:
        """`autostart=False` оставляет сервер оболочкой над `.app`: `HttpProvider` его не сервит."""
        self._cfg = cfg
        self._log = log if log is not None else nop()
        self.autostart = autostart
        self.app = build_app(
            cfg,
            self._log,
            store=store,
            auth=auth,
            probes=probes,
            middlewares=middlewares,
            security_schemes=security_schemes,
            lifespan=lifespan,
            instr=instr,
        )
        self._socket: socket.socket | None = None
        self._serve_task: asyncio.Task[None] | None = None
        self._stopped = False
        # uvicorn принимает только целые секунды: округляем вверх, иначе конфиг вроде
        # HTTP_SHUTDOWN_TIMEOUT=0.5 усекался бы в 0 — «гасить без ожидания».
        self._uvicorn = uvicorn.Server(
            uvicorn.Config(
                self.app,
                timeout_keep_alive=math.ceil(cfg.idle_timeout),
                timeout_graceful_shutdown=math.ceil(cfg.shutdown_timeout),
                log_config=None,
                access_log=False,
            )
        )

    def listen(self) -> None:
        """Занимает порт синхронно и идемпотентно; занятый адрес — `ListenError`, не строка лога."""
        if self._socket is not None:
            return
        host, port = split_addr(self._cfg.addr)
        try:
            sock = bind_socket(host, port)
        except OSError as exc:
            raise ListenError(f"httpkit: listen {self._cfg.addr}: {exc}") from exc
        sock.listen(2048)
        sock.set_inheritable(True)
        self._socket = sock

    def addr(self) -> str:
        """Фактический `host:port` после `listen` (`[::]:8080` при bind-all); до него — пусто."""
        if self._socket is None:
            return ""
        host, port = self._socket.getsockname()[:2]
        return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"

    async def serve(self) -> None:
        """Обслуживает соединения до `stop`; при необходимости вызывает `listen`.

        После `stop` не поднимается: остановленный сервер не должен занять порт заново.
        """
        if self._stopped:
            return
        self.listen()
        assert self._socket is not None
        self._serve_task = asyncio.current_task()
        try:
            await self._uvicorn.serve(sockets=[self._socket])
        finally:
            self._serve_task = None

    async def stop(self) -> None:
        """Просит uvicorn завершиться и ждёт `shutdown_timeout`, затем снимает задачу."""
        self._stopped = True
        self._uvicorn.should_exit = True
        task = self._serve_task
        if task is None or task is asyncio.current_task():
            self._close_socket()
            return
        try:
            async with asyncio.timeout(self._cfg.shutdown_timeout):
                await asyncio.shield(task)
        except TimeoutError:
            task.cancel()
        finally:
            self._close_socket()

    def _close_socket(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None
