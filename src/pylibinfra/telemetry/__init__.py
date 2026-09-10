"""telemetry — opt-in bootstrap OTel SDK: провайдеры трёх сигналов, OTLP/HTTP-экспорт,
resource-атрибуты, W3C-propagator и system-метрики. Без него инструментация
остальных китов инертна (no-op)."""

from .config import Config, ConfigError, new_config, signal_url
from .init import Providers, init
from .module import TelemetryProvider, start

__all__ = [
    "Config",
    "ConfigError",
    "Providers",
    "TelemetryProvider",
    "init",
    "new_config",
    "signal_url",
    "start",
]
