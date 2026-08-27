"""httpclient — исходящий HTTP-клиент (httpx) с retry, circuit breaker, OTel и
авто-прокидыванием X-Request-ID; ApiClient — типизированный JSON-слой поверх него
(model=-декод в wire-модели, typed-таксономия ошибок, Idempotency-Key)."""

from .api import (
    HEADER_IDEMPOTENCY_KEY,
    ApiClient,
    ApiClients,
    ApiError,
    AuthError,
    ConflictError,
    ContractError,
    ForbiddenError,
    InvalidRequestError,
    NotFoundError,
    RateLimitedError,
    UnavailableError,
    UnknownBackendError,
)
from .breaker import BreakerConfig, BreakerOpenError
from .client import Client
from .config import BackendBreakerConfig, BackendConfig, BackendsConfig, Config
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
    "AuthError",
    "BackendBreakerConfig",
    "BackendConfig",
    "BackendsConfig",
    "BreakerConfig",
    "BreakerOpenError",
    "Client",
    "Config",
    "ConflictError",
    "ContractError",
    "ForbiddenError",
    "HttpClientProvider",
    "InvalidRequestError",
    "NotFoundError",
    "RateLimitedError",
    "UnavailableError",
    "UnknownBackendError",
    "new_backends_config",
    "new_breaker_config",
    "new_config",
    "request_id",
]
