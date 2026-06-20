"""grpckit — gRPC-сервер на grpc.aio с готовой обвязкой: OTel-трейс-интерсептор,
observability-интерсептор (метрики + access-log + recovery), стандартный
grpc.health.v1, bind UDS и/или TCP, graceful-drain. gRPC-аналог httpkit. Эмитит
grpc_server_requests_total, grpc_server_request_duration_seconds,
grpc_server_panics_total. Транспортная зависимость — extra [grpckit].
"""

from .config import Config, new_config
from .health import OVERALL, Health, Probe
from .interceptors import ObservabilityInterceptor
from .metrics import GRPCInstruments, new_instruments
from .module import GrpcProvider, ProbeBundle, ServicerBundle
from .server import Server, ServicerRegistrar, build_interceptors

__all__ = [
    "OVERALL",
    "Config",
    "GRPCInstruments",
    "GrpcProvider",
    "Health",
    "ObservabilityInterceptor",
    "Probe",
    "ProbeBundle",
    "Server",
    "ServicerBundle",
    "ServicerRegistrar",
    "build_interceptors",
    "new_config",
    "new_instruments",
]
