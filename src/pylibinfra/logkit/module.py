from dishka import Provider, Scope, provide

from .config import Config
from .logger import Logger
from .redact import RedactConfig
from .service import ServiceConfig
from .structlog_impl import configure, new_logger


# Provider не provide'ит Config / ServiceConfig / RedactConfig — caller
# регистрирует их сам (asymmetry note, см. pgkit/module.py). Дешёвый mode —
# new_*-фабрики ниже. Meter и LoggerProvider logkit берёт из глобальных OTel-
# providers (их выставляет telemetry.init) — так logkit работает и без telemetry
# в DI (тогда вывод идёт только в stdout, OTel-handler no-op).
class LogkitProvider(Provider):
    scope = Scope.APP

    @provide(provides=Logger)
    def logger(self, cfg: Config, redact_cfg: RedactConfig) -> Logger:
        configure(cfg, redact_cfg=redact_cfg)
        return new_logger()


def new_config() -> Config:
    return Config()


def new_service_config() -> ServiceConfig:
    return ServiceConfig()


def new_redact_config() -> RedactConfig:
    return RedactConfig()
