"""httpclient — исходящий HTTP-клиент (httpx) с retry, circuit breaker, OTel и
авто-прокидыванием X-Request-ID; ApiClient — типизированный JSON-слой поверх него."""

from .api import (
    HEADER_IDEMPOTENCY_KEY,
    ApiClient,
    ApiClients,
    ApiError,
    ConflictError,
    NotFoundError,
    UnavailableError,
)
from .breaker import BreakerConfig, BreakerOpenError
from .client import Client
from .config import BackendsConfig, Config
from .module import (
    ApiClientsProvider,
    HttpClientProvider,
    new_backends_config,
    new_breaker_config,
    new_config,
)
from .reqid import HEADER_REQUEST_ID, request_id

__all__ = [
    "HEADER_IDEMPOTENCY_KEY",
    "HEADER_REQUEST_ID",
    "ApiClient",
    "ApiClients",
    "ApiClientsProvider",
    "ApiError",
    "BackendsConfig",
    "BreakerConfig",
    "BreakerOpenError",
    "Client",
    "Config",
    "ConflictError",
    "HttpClientProvider",
    "NotFoundError",
    "UnavailableError",
    "new_backends_config",
    "new_breaker_config",
    "new_config",
    "request_id",
]
