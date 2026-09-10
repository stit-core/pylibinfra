"""grpckit — gRPC-сервер на grpc.aio: интерсепторы, health и lifecycle транспорта.

Цепочка интерсепторов — request-id, access-log, recovery, опц. auth; спан даёт
otel-grpc-instrumentation. Эмитит `grpc_server_requests_total`,
`grpc_server_request_duration_seconds`, `grpc_server_panics_total`.
Транспортная зависимость — extra `[grpckit]`.
"""

from .auth import AUTHORIZATION_KEY, Authenticator, AuthInterceptor, identity
from .config import DEFAULT_ADDR, DEFAULT_MAX_MSG_SIZE, Config, ConfigError, new_config
from .health import OVERALL, Health
from .interceptors import (
    INTERNAL_MESSAGE,
    REQUEST_ID_KEY,
    AccessLogInterceptor,
    HandlerInterceptor,
    RecoveryInterceptor,
    RequestIDInterceptor,
    request_id,
)
from .metrics import Instruments, new_instruments
from .module import GrpcProvider, OptionalAuthenticator
from .server import (
    ListenError,
    Server,
    ServiceNames,
    ServiceRegistrar,
    Services,
    TraceFilter,
    build_interceptors,
    tcp_target,
)

__all__ = [
    "AUTHORIZATION_KEY",
    "DEFAULT_ADDR",
    "DEFAULT_MAX_MSG_SIZE",
    "INTERNAL_MESSAGE",
    "OVERALL",
    "REQUEST_ID_KEY",
    "AccessLogInterceptor",
    "AuthInterceptor",
    "Authenticator",
    "Config",
    "ConfigError",
    "GrpcProvider",
    "HandlerInterceptor",
    "Health",
    "Instruments",
    "ListenError",
    "OptionalAuthenticator",
    "RecoveryInterceptor",
    "RequestIDInterceptor",
    "Server",
    "ServiceNames",
    "ServiceRegistrar",
    "Services",
    "TraceFilter",
    "build_interceptors",
    "identity",
    "new_config",
    "new_instruments",
    "request_id",
    "tcp_target",
]
