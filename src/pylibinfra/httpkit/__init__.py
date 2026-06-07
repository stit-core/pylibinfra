"""httpkit — HTTP-сервер на FastAPI с готовой middleware-цепочкой (CORS,
RateLimit, MaxBody, RequestID, Recovery, SecurityHeaders, AccessLog, Metrics,
OTel, опц. Auth/Idempotency) и унифицированной error-моделью APIError
({code,status,title,detail}, Problem+JSON). Эмитит http_requests_total,
http_request_duration_seconds, http_panics_total, http_max_body_rejections_total,
http_idempotency_total.
"""

from .auth import AuthExtractor, AuthMiddleware
from .config import Config, new_config
from .context import HEADER_REQUEST_ID, request_id
from .cors import CORSConfig, apply_cors
from .error import (
    PROBLEM_CONTENT_TYPE,
    APIError,
    ErrorMapping,
    bad_request,
    conflict,
    error,
    forbidden,
    internal,
    map_err,
    map_sentinel,
    not_found,
    register_error_handlers,
    unauthorized,
)
from .health import Probe, register_health
from .idempotency import IdempotencyConfig, IdempotencyMiddleware, IdempotencyStore
from .maxbody import MaxBodyConfig, MaxBodyMiddleware
from .metrics import HTTPInstruments, new_instruments
from .middleware import (
    AccessLogMiddleware,
    MetricsMiddleware,
    RecoveryMiddleware,
    RequestIDMiddleware,
)
from .module import HttpProvider, OptionalAuth, OptionalStore, ProbeBundle, setup
from .ratelimit import RateLimitConfig, RateLimitMiddleware
from .security import SecurityHeadersConfig, SecurityHeadersMiddleware
from .server import Server, build_app

__all__ = [
    "HEADER_REQUEST_ID",
    "PROBLEM_CONTENT_TYPE",
    "APIError",
    "AccessLogMiddleware",
    "AuthExtractor",
    "AuthMiddleware",
    "CORSConfig",
    "Config",
    "ErrorMapping",
    "HTTPInstruments",
    "HttpProvider",
    "IdempotencyConfig",
    "IdempotencyMiddleware",
    "IdempotencyStore",
    "MaxBodyConfig",
    "MaxBodyMiddleware",
    "MetricsMiddleware",
    "OptionalAuth",
    "OptionalStore",
    "Probe",
    "ProbeBundle",
    "RateLimitConfig",
    "RateLimitMiddleware",
    "RecoveryMiddleware",
    "RequestIDMiddleware",
    "SecurityHeadersConfig",
    "SecurityHeadersMiddleware",
    "Server",
    "apply_cors",
    "bad_request",
    "build_app",
    "conflict",
    "error",
    "forbidden",
    "internal",
    "map_err",
    "map_sentinel",
    "new_config",
    "new_instruments",
    "not_found",
    "register_error_handlers",
    "register_health",
    "request_id",
    "setup",
    "unauthorized",
]
