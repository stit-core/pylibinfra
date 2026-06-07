"""httpclient — исходящий HTTP-клиент (httpx) с retry, circuit breaker, OTel и
авто-прокидыванием X-Request-ID."""

from .breaker import BreakerConfig, BreakerOpenError
from .client import Client
from .config import Config
from .module import HttpClientProvider, new_breaker_config, new_config
from .reqid import HEADER_REQUEST_ID, request_id

__all__ = [
    "HEADER_REQUEST_ID",
    "BreakerConfig",
    "BreakerOpenError",
    "Client",
    "Config",
    "HttpClientProvider",
    "new_breaker_config",
    "new_config",
    "request_id",
]
