"""Классификация gRPC-кодов: за каким исходом стоит сбой сервера."""

from __future__ import annotations

import grpc

_SERVER_FAULTS = frozenset(
    {
        grpc.StatusCode.INTERNAL,
        grpc.StatusCode.UNKNOWN,
        grpc.StatusCode.DATA_LOSS,
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.UNIMPLEMENTED,
    }
)


def is_server_fault(code: grpc.StatusCode) -> bool:
    """Сбой сервера (или транспорта до него) — остальные не-OK исходы ожидаемы."""
    return code in _SERVER_FAULTS
