"""Инструменты серверных интерсепторов: RED-метрики gRPC-сервера."""

from __future__ import annotations

from dataclasses import dataclass

from opentelemetry.metrics import Counter, Histogram

from pylibinfra._internal import otelkit


@dataclass(frozen=True, slots=True)
class Instruments:
    """Набор инструментов кита; создаётся вместе с сервером, а не на уровне модуля."""

    requests: Counter
    duration: Histogram
    panics: Counter


def new_instruments() -> Instruments:
    """Собирает инструменты на meter'е `pylibinfra.grpckit` (no-op без MeterProvider)."""
    meter = otelkit.meter("grpckit")
    return Instruments(
        requests=meter.create_counter(
            "grpc_server_requests_total",
            description="gRPC server RPCs by method and status code",
        ),
        duration=otelkit.duration_histogram(
            meter,
            "grpc_server_request_duration_seconds",
            "gRPC server RPC duration",
        ),
        panics=meter.create_counter(
            "grpc_server_panics_total",
            description="Unhandled handler exceptions recovered by the kit",
        ),
    )
