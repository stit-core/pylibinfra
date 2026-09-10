"""dishka-проводка logkit: `Logger` из `Config` и опциональных redact/OTel-provider'а."""

from __future__ import annotations

from dishka import Provider, Scope, provide
from opentelemetry._logs import LoggerProvider

from .build import new
from .config import Config, RedactConfig
from .logger import Logger
from .stdlib import capture_stdlib

# Тип-обёртка вместо optional-инъекции: дефолт кита перекрывает провайдер,
# зарегистрированный ПОЗЖЕ (последний в контейнере побеждает).
OptionalRedactConfig = RedactConfig | None
OptionalLoggerProvider = LoggerProvider | None


# Provider не provide'ит Config / ServiceConfig (asymmetry note) — caller
# регистрирует их сам. Дешёвый mode — new_config()/new_service_config().
class LogkitProvider(Provider):
    """Provide'ит `Logger`; `RedactConfig` и OTel `LoggerProvider` — опциональные override'ы."""

    scope = Scope.APP

    @provide
    def default_redact(self) -> OptionalRedactConfig:
        """Дефолт опциональной зависимости: redact выключен."""
        return None

    @provide
    def default_logger_provider(self) -> OptionalLoggerProvider:
        """Дефолт опциональной зависимости: OTel-sink не подключается."""
        return None

    @provide
    def logger(
        self,
        cfg: Config,
        redact: OptionalRedactConfig,
        otel: OptionalLoggerProvider,
    ) -> Logger:
        """Собирает `Logger` и уводит в него корневой `logging` (uvicorn/grpc/aiogram)."""
        log = new(cfg, redact=redact, otel=otel)
        capture_stdlib(log, cfg)
        return log
