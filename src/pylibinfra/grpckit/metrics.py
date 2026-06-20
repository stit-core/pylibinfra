"""GRPCInstruments — OTel-инструменты серверных интерсепторов. Берёт глобальный
meter `metrics.get_meter("app")` (telemetry.init выставляет provider; без
telemetry — no-op). Имена: grpc_server_requests_total,
grpc_server_request_duration_seconds, grpc_server_panics_total.
"""

from __future__ import annotations

from opentelemetry import metrics
from opentelemetry.metrics import Counter, Histogram, Meter

_DURATION_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30]


class GRPCInstruments:
    req_total: Counter
    req_duration: Histogram
    panics_total: Counter

    def __init__(self, meter: Meter) -> None:
        self.req_total = meter.create_counter(
            "grpc_server_requests_total",
            description="Total number of gRPC server RPCs by method and status code",
        )
        self.req_duration = meter.create_histogram(
            "grpc_server_request_duration_seconds",
            description="gRPC server RPC handler duration in seconds",
            explicit_bucket_boundaries_advisory=_DURATION_BUCKETS,
        )
        self.panics_total = meter.create_counter(
            "grpc_server_panics_total",
            description="Unhandled exceptions recovered in gRPC handler chain",
        )


def new_instruments() -> GRPCInstruments:
    return GRPCInstruments(metrics.get_meter("app"))
