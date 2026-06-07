"""telemetry — bootstrap OTel SDK (trace/meter/log), OTLP exporters, span()."""

from .config import Config
from .decorator import span
from .init import Providers, init
from .module import TelemetryProvider, new_config

__all__ = ["Config", "Providers", "TelemetryProvider", "init", "new_config", "span"]
