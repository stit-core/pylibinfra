"""httpkit — HTTP-сервер на FastAPI с готовой middleware-цепочкой и единой error-моделью.

Из коробки: RequestID, OTel, AccessLog, Metrics, Recovery, SecurityHeaders, CORS, RateLimit,
MaxBody, опц. Idempotency и Auth; ошибки — `APIError` (problem+json), `/health` и `/ready` —
по пробам healthkit. Эмитит `http_requests_total`, `http_request_duration_seconds`,
`http_panics_total`, `http_max_body_rejections_total`, `http_rate_limited_total`,
`http_idempotency_total` и server-спан `METHOD /route/{pattern}`.
"""

from pylibinfra._internal.requestid import HEADER as HEADER_REQUEST_ID
from pylibinfra._internal.requestid import current as request_id

from .auth import (
    AUTH_DONE_KEY,
    AuthExtractor,
    AuthMiddleware,
    AuthUnavailableError,
    bind_subject,
    subject,
    unbind_subject,
)
from .config import Config, ConfigError, new_config
from .cors import CORSConfig, CORSMiddleware
from .error import (
    PROBLEM_CONTENT_TYPE,
    APIError,
    ErrorMapping,
    HttpkitError,
    bad_request,
    code_for_status,
    conflict,
    error,
    forbidden,
    internal,
    map_as,
    map_err,
    map_is,
    not_found,
    register_error_handlers,
    unauthorized,
    unavailable,
)
from .health import HEALTH_PATH, READY_PATH, Probe, register_health
from .idempotency import (
    HEADER_IDEMPOTENT_REPLAYED,
    IdempotencyConfig,
    IdempotencyMiddleware,
    IdempotencyStore,
    MemoryStore,
)
from .lifespan import Lifespan, dishka_lifespan
from .maxbody import MaxBodyConfig, MaxBodyError, MaxBodyMiddleware
from .metrics import MetricsMiddleware
from .middleware import (
    AccessLogMiddleware,
    OTelMiddleware,
    RecoveryMiddleware,
    RequestIDMiddleware,
)
from .module import (
    HttpProvider,
    OptionalAuth,
    OptionalSecuritySchemes,
    OptionalStore,
    setup,
)
from .ratelimit import RateLimitConfig, RateLimiter, RateLimitMiddleware, client_ip
from .security import SecurityHeadersConfig, SecurityHeadersMiddleware
from .server import ListenError, Middlewares, SecuritySchemes, Server, build_app
from .webhook_secret import SharedSecretConfig, shared_secret_guard

__all__ = [
    "AUTH_DONE_KEY",
    "HEADER_IDEMPOTENT_REPLAYED",
    "HEADER_REQUEST_ID",
    "HEALTH_PATH",
    "PROBLEM_CONTENT_TYPE",
    "READY_PATH",
    "APIError",
    "AccessLogMiddleware",
    "AuthExtractor",
    "AuthMiddleware",
    "AuthUnavailableError",
    "CORSConfig",
    "CORSMiddleware",
    "Config",
    "ConfigError",
    "ErrorMapping",
    "HttpProvider",
    "HttpkitError",
    "IdempotencyConfig",
    "IdempotencyMiddleware",
    "IdempotencyStore",
    "Lifespan",
    "ListenError",
    "MaxBodyConfig",
    "MaxBodyError",
    "MaxBodyMiddleware",
    "MemoryStore",
    "MetricsMiddleware",
    "Middlewares",
    "OTelMiddleware",
    "OptionalAuth",
    "OptionalSecuritySchemes",
    "OptionalStore",
    "Probe",
    "RateLimitConfig",
    "RateLimitMiddleware",
    "RateLimiter",
    "RecoveryMiddleware",
    "RequestIDMiddleware",
    "SecurityHeadersConfig",
    "SecurityHeadersMiddleware",
    "SecuritySchemes",
    "Server",
    "SharedSecretConfig",
    "bad_request",
    "bind_subject",
    "build_app",
    "client_ip",
    "code_for_status",
    "conflict",
    "dishka_lifespan",
    "error",
    "forbidden",
    "internal",
    "map_as",
    "map_err",
    "map_is",
    "new_config",
    "not_found",
    "register_error_handlers",
    "register_health",
    "request_id",
    "setup",
    "shared_secret_guard",
    "subject",
    "unauthorized",
    "unavailable",
    "unbind_subject",
]
