"""GrpcProvider — dishka-Provider grpckit. Поднимает Server в Scope.APP (start на
вход, graceful stop на выход контейнера). Опциональные зависимости (Logger,
servicer'ы, probe'ы) выражены типами-обёртками с пустыми дефолтами: consumer
override'ит их, регистрируя свой Provider ПОСЛЕ GrpcProvider (последний в
контейнере побеждает) — тот же приём, что в httpkit.

Provider не provide'ит Config (asymmetry note) — caller регистрирует сам;
дешёвый mode — new_config().
"""

from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide

from pylibinfra.logkit import Logger, nop

from .config import Config
from .health import Probe
from .server import Server, ServicerRegistrar

# Набор регистраторов servicer'ов консьюмера (override'ится App-Provider'ом).
ServicerBundle = tuple[ServicerRegistrar, ...]
# Набор readiness-probe'ов (override'ится App-Provider'ом).
ProbeBundle = tuple[Probe, ...]


class GrpcProvider(Provider):
    scope = Scope.APP

    @provide
    def default_logger(self) -> Logger:
        return nop()

    @provide
    def default_registrars(self) -> ServicerBundle:
        return ()

    @provide
    def default_probes(self) -> ProbeBundle:
        return ()

    @provide
    async def server(
        self,
        cfg: Config,
        logger: Logger,
        registrars: ServicerBundle,
        probes: ProbeBundle,
    ) -> AsyncIterator[Server]:
        srv = Server(cfg, logger, registrars=registrars, probes=probes)
        await srv.start()
        yield srv
        await srv.stop()
