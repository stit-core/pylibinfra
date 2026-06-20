"""Health — стандартный grpc.health.v1 (тот же контракт, что grpc_health_probe и
k8s grpc-проба). Обёртка над aio-HealthServicer: регистрация в сервере, перевод
overall-статуса SERVING/NOT_SERVING, graceful-shutdown (всё в NOT_SERVING при
drain). Probe — опц. readiness-проверки, гоняются один раз на старте: упала —
overall остаётся NOT_SERVING.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

# Пустой service-ключ = статус всего сервера (whole-server health).
OVERALL = ""


@runtime_checkable
class Probe(Protocol):
    async def __call__(self) -> None: ...


class Health:
    def __init__(self) -> None:
        self._servicer = health.aio.HealthServicer()

    def register(self, server: grpc.aio.Server) -> None:
        health_pb2_grpc.add_HealthServicer_to_server(self._servicer, server)

    async def set_serving(self, service: str = OVERALL) -> None:
        await self._servicer.set(service, health_pb2.HealthCheckResponse.SERVING)

    async def set_not_serving(self, service: str = OVERALL) -> None:
        await self._servicer.set(service, health_pb2.HealthCheckResponse.NOT_SERVING)

    async def shutdown(self) -> None:
        # Переводит все сервисы в NOT_SERVING и игнорирует дальнейшие set —
        # клиенты/балансер видят drain до фактического закрытия портов.
        await self._servicer.enter_graceful_shutdown()
