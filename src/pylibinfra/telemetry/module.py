"""dishka-проводка telemetry: `Providers` с graceful-остановкой и OTel-`LoggerProvider`."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from dishka import AsyncContainer, Provider, Scope, provide
from opentelemetry._logs import LoggerProvider

from pylibinfra import logkit

from .config import Config
from .init import Providers, init


# Provider не provide'ит Config (asymmetry note) и не занимает в графе
# trace.Tracer/metrics.Meter — инструменты киты берут у глобальных провайдеров.
class TelemetryProvider(Provider):
    """Provide'ит `Providers` и OTel-`LoggerProvider` (второй sink `logkit`)."""

    scope = Scope.APP

    @provide
    def default_redact(self) -> logkit.OptionalRedactConfig:
        """Дефолт опциональной зависимости: логгер ошибок экспорта без redact'а."""
        return None

    @provide
    async def providers(
        self,
        cfg: Config,
        log_cfg: logkit.Config,
        redact: logkit.OptionalRedactConfig,
    ) -> AsyncIterator[Providers]:
        """Поднимает SDK и гасит его на закрытии контейнера, деля бюджет между сигналами."""
        # Логгер ошибок — без OTel-sink'а: иначе ошибка экспорта логов замкнулась бы в петлю.
        providers = init(cfg, error_logger=logkit.new(log_cfg, redact=redact))
        try:
            yield providers
        finally:
            # Сбой соседнего финализатора не должен оставить SDK с живыми потоками.
            await asyncio.to_thread(providers.shutdown, cfg.export_timeout * 3)

    @provide
    def logger_provider(self, providers: Providers) -> LoggerProvider:
        """OTel-`LoggerProvider` для тех, кто мостит свои логи в OTLP."""
        return providers.logger_provider

    @provide
    def logkit_sink(self, providers: Providers) -> logkit.OptionalLoggerProvider:
        """Перекрывает дефолт `LogkitProvider`: логи уходят и в OTLP."""
        return providers.logger_provider


async def start(container: AsyncContainer) -> Providers:
    """Поднимает SDK на старте приложения — до первого обращения к остальным китам.

    В dishka ресурс не создаётся, пока его не запросят: без этого вызова киты
    инструментируются в no-op, а `shutdown` не вешается на закрытие контейнера.
    """
    return await container.get(Providers)
