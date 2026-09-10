"""httpclient — исходящий HTTP-клиент: retry, circuit breaker на хост, редиректы, SSE.

Поверх — `ApiClient`: JSON-декод в wire-модель и таксономия typed-ошибок.
Эмитит `http_client_request_duration_seconds`, `http_client_retry_attempts_total`,
`http_client_breaker_transitions_total`, `http_client_breaker_open` и спаны
`httpclient.Do <METHOD>` / `httpclient.Stream <METHOD>`.
"""

from .api import (
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
from .breaker import State
from .client import (
    HEADER_IDEMPOTENCY_KEY,
    MAX_RETRY_AFTER,
    BeforeAttempt,
    Client,
    allow_retry,
    new,
)
from .config import (
    BackendBreakerConfig,
    BackendConfig,
    BackendsConfig,
    BreakerConfig,
    Config,
    new_backends_config,
    new_breaker_config,
    new_config,
    new_config_prefixed,
)
from .errors import (
    BreakerOpenError,
    ConfigError,
    CrossHostRedirectError,
    EventTooLargeError,
    HeaderTimeoutError,
    HttpClientError,
    NotEventStreamError,
    StatusError,
    TooManyRedirectsError,
)
from .module import ApiClientsProvider, HttpClientProvider, OptionalBeforeAttempt, OptionalTransport
from .retryafter import parse_retry_after
from .sse import Event, SSEStream

__all__ = [
    "HEADER_IDEMPOTENCY_KEY",
    "MAX_RETRY_AFTER",
    "ApiClient",
    "ApiClients",
    "ApiClientsProvider",
    "ApiError",
    "AuthError",
    "BackendBreakerConfig",
    "BackendConfig",
    "BackendsConfig",
    "BeforeAttempt",
    "BreakerConfig",
    "BreakerOpenError",
    "Client",
    "Config",
    "ConfigError",
    "ConflictError",
    "ContractError",
    "CrossHostRedirectError",
    "Event",
    "EventTooLargeError",
    "ForbiddenError",
    "HeaderTimeoutError",
    "HttpClientError",
    "HttpClientProvider",
    "InvalidRequestError",
    "NotEventStreamError",
    "NotFoundError",
    "OptionalBeforeAttempt",
    "OptionalTransport",
    "RateLimitedError",
    "SSEStream",
    "State",
    "StatusError",
    "TooManyRedirectsError",
    "UnavailableError",
    "UnknownBackendError",
    "allow_retry",
    "new",
    "new_backends_config",
    "new_breaker_config",
    "new_config",
    "new_config_prefixed",
    "parse_retry_after",
]
