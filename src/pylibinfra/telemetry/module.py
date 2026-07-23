from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide
from opentelemetry import metrics, trace
from opentelemetry.metrics import Meter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.trace import Tracer

from .config import Config
from .init import Providers, init


# Provider не provide'ит Config (asymmetry note, см. pgkit/module.py) — caller
# регистрирует Config сам. Дешёвый mode: new_config() ниже.
class TelemetryProvider(Provider):
    scope = Scope.APP

    @provide
    async def providers(self, cfg: Config) -> AsyncIterator[Providers]:
        p = init(cfg)
        yield p
        p.shutdown()

    @provide
    def tracer(self, _: Providers) -> Tracer:
        return trace.get_tracer("app")

    @provide
    def meter(self, _: Providers) -> Meter:
        return metrics.get_meter("app")

    @provide
    def logger_provider(self, p: Providers) -> LoggerProvider:
        return p.logger_provider


def new_config() -> Config:
    return Config()
