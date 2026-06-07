from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI

from pylibinfra.logkit import Logger, nop

from .auth import AuthExtractor
from .config import Config
from .health import Probe
from .idempotency import IdempotencyStore
from .server import Server

# Опциональные зависимости httpkit (Store/Auth/Probes) выражены типами-обёртками:
# dishka не поддерживает optional-инъекцию по `X | None`, но позволяет
# override'ить фабрику той же сигнатуры (последний провайдер в контейнере
# побеждает). HttpProvider даёт пустые дефолты ниже; consumer регистрирует свой
# провайдер ПОСЛЕ HttpProvider, чтобы подменить нужный.
OptionalStore = IdempotencyStore | None
OptionalAuth = AuthExtractor | None
ProbeBundle = tuple[Probe, ...]


# Provider не provide'ит Config (asymmetry note, см. logkit/cachekit) — caller
# регистрирует Config сам. Дешёвый mode: new_config(). Meter httpkit не берёт из
# DI: middleware читают глобальный metrics.get_meter("app") (no-op без telemetry).
class HttpProvider(Provider):
    scope = Scope.APP

    @provide
    def default_logger(self) -> Logger:
        return nop()

    @provide
    def default_store(self) -> OptionalStore:
        return None

    @provide
    def default_auth(self) -> OptionalAuth:
        return None

    @provide
    def default_probes(self) -> ProbeBundle:
        return ()

    @provide
    async def server(
        self,
        cfg: Config,
        logger: Logger,
        store: OptionalStore,
        auth: OptionalAuth,
        probes: ProbeBundle,
    ) -> AsyncIterator[Server]:
        srv = Server(cfg, logger, store=store, auth=auth, probes=probes)
        await srv.start()
        yield srv
        await srv.stop()

    @provide
    def app(self, server: Server) -> FastAPI:
        return server.app


def setup(app: FastAPI, container: object) -> None:
    """Прокидывает dishka-container в FastAPI (FromDishka в роутах). Тонкая
    обёртка над dishka.integrations.fastapi.setup_dishka — чтобы consumer'у не
    тянуть интеграцию напрямую."""
    setup_dishka(container, app)  # type: ignore[arg-type]
