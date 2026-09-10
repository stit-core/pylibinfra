"""GrpcProvider — `Server` с lifecycle в dishka-контейнере."""

from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide

from pylibinfra.healthkit import Probes
from pylibinfra.logkit import Logger

from .auth import Authenticator
from .config import Config
from .server import Server, ServiceNames, Services

# Опциональные зависимости выражены типами-обёрткой и пустыми дефолтами: консьюмер
# регистрирует свой Provider ПОСЛЕ GrpcProvider и перекрывает нужный (последний
# провайдер в контейнере побеждает).
OptionalAuthenticator = Authenticator | None


class GrpcProvider(Provider):
    """Provider не provide'ит `Config` (asymmetry note) — его регистрирует консьюмер."""

    scope = Scope.APP

    @provide
    def default_services(self) -> Services:
        """Пустой набор регистраторов; консьюмер перекрывает его своим Provider'ом."""
        return ()

    @provide
    def default_service_names(self) -> ServiceNames:
        """Пустой набор имён для reflection; без них reflection отдаёт только health."""
        return ServiceNames(())

    @provide
    def default_probes(self) -> Probes:
        """Пустой набор health-проб; консьюмер собирает свой кортеж из проб китов."""
        return ()

    @provide
    def default_authenticator(self) -> OptionalAuthenticator:
        """Без `Authenticator` в контейнере auth-интерсептор не навешивается."""
        return None

    @provide
    async def server(
        self,
        cfg: Config,
        log: Logger,
        services: Services,
        service_names: ServiceNames,
        probes: Probes,
        authenticator: OptionalAuthenticator,
    ) -> AsyncIterator[Server]:
        """Сервер слушает от получения из контейнера до его закрытия."""
        server = Server(
            cfg,
            log,
            services=services,
            probes=probes,
            authenticator=authenticator,
            service_names=service_names,
        )
        await server.serve()
        yield server
        await server.stop()
