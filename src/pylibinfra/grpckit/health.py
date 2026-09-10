"""Стандартный `grpc.health.v1` и переоценка overall-статуса по пробам зависимостей."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from pylibinfra.healthkit import Probes, with_timeout
from pylibinfra.logkit import Logger

from .config import Config

# Пустой ключ сервиса = статус всего сервера (whole-server health).
OVERALL = ""
SERVICE_NAME = health_pb2.DESCRIPTOR.services_by_name["Health"].full_name

_SERVING = health_pb2.HealthCheckResponse.SERVING
_NOT_SERVING = health_pb2.HealthCheckResponse.NOT_SERVING
_SERVICE_UNKNOWN = health_pb2.HealthCheckResponse.SERVICE_UNKNOWN


class _Servicer(health.aio.HealthServicer):  # type: ignore[misc]
    """Стандартный health-сервис, у которого `Watch` — async-генератор.

    Upstream пишет ответы через `context.write`, а интерсепторы (в том числе
    otel-grpc) такую форму handler'а не разбирают: логика watch'а та же, отдача —
    через `yield`.
    """

    async def Watch(self, request: Any, context: Any) -> AsyncIterator[Any]:  # noqa: N802
        """Шлёт статус сервиса при каждом его изменении, пока клиент держит стрим."""
        condition = self._server_watchers[request.service]
        last_status = None
        try:
            async with condition:
                while True:
                    status = self._server_status.get(request.service, _SERVICE_UNKNOWN)
                    if status != last_status:
                        yield health_pb2.HealthCheckResponse(status=status)
                    last_status = status
                    await condition.wait()
        finally:
            self._server_watchers.pop(request.service, None)


class Health:
    """Health-сервис сервера: ручной статус плюс цикл переоценки проб."""

    def __init__(self, cfg: Config, log: Logger, probes: Probes = ()) -> None:
        self._cfg = cfg
        self._log = log
        self._probes = probes
        self._servicer = _Servicer()
        self._task: asyncio.Task[None] | None = None

    def register(self, server: Any) -> None:
        """Вешает health-сервис на сервер; вызывается до старта."""
        health_pb2_grpc.add_HealthServicer_to_server(self._servicer, server)

    async def set_serving(self, service: str, ok: bool) -> None:
        """Ставит статус сервиса (`OVERALL` — весь сервер); после `stop` вызов игнорируется."""
        await self._servicer.set(service, _SERVING if ok else _NOT_SERVING)

    def start(self) -> None:
        """Поднимает цикл переоценки; без проб или при `GRPC_HEALTH_INTERVAL=0` — no-op."""
        if self._task is not None or not self._probes or self._cfg.health_interval <= 0:
            return
        self._task = asyncio.create_task(self._watch(), name="grpckit-health")

    async def stop(self) -> None:
        """Снимает цикл и переводит все сервисы в NOT_SERVING до закрытия портов."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._servicer.enter_graceful_shutdown()

    async def _watch(self) -> None:
        serving = True
        while True:
            serving = await self._evaluate(serving)
            await asyncio.sleep(self._cfg.health_interval)

    async def _evaluate(self, was: bool) -> bool:
        failed = await self._run_probes()
        serving = failed is None
        # Логируется смена статуса, а не каждый тик: иначе цикл писал бы строку в лог
        # каждые GRPC_HEALTH_INTERVAL.
        if serving == was:
            return serving
        await self.set_serving(OVERALL, serving)
        if failed is None:
            self._log.info("grpckit: health serving")
        else:
            self._log.warn("grpckit: health not serving", probe=failed[0], error=failed[1])
        return serving

    async def _run_probes(self) -> tuple[str, str] | None:
        bounded = [with_timeout(probe, self._cfg.health_probe_timeout) for probe in self._probes]
        results = await asyncio.gather(*(probe.run() for probe in bounded), return_exceptions=True)
        for probe, result in zip(self._probes, results, strict=True):
            if isinstance(result, BaseException):
                return probe.name, str(result) or type(result).__name__
        return None
