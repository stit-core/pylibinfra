"""Server — собирает grpc.aio-сервер: интерсепторы (OTel-трейс снаружи +
observability внутри), стандартный health, servicer'ы консьюмера; биндит UDS
и/или TCP; start() поднимает + гоняет readiness-probe'ы и выставляет SERVING,
stop() делает graceful-drain (health→NOT_SERVING, дать активным RPC доиграть до
grace, затем закрыть). UDS-сокет снимается перед bind и при остановке.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import grpc
from opentelemetry.instrumentation.grpc import aio_server_interceptor

from pylibinfra.logkit import Logger, nop

from .config import Config
from .health import Health, Probe
from .interceptors import ObservabilityInterceptor
from .metrics import GRPCInstruments, new_instruments

# Консьюмер передаёт add_<Svc>Servicer_to_server как partial(servicer, ...):
# kit не знает сгенерированных типов, регистрацию делает сам сервис.
ServicerRegistrar = Callable[[grpc.aio.Server], None]


def build_interceptors(
    log: Logger,
    instruments: GRPCInstruments,
) -> list[grpc.aio.ServerInterceptor]:
    # Порядок = снаружи-внутрь: OTel-спан оборачивает весь RPC, наш
    # observability/recovery — внутри спана (исключения видны и трейсу, и метрике).
    # aio_server_interceptor — без аннотаций в otel-grpc-instrumentation.
    otel = aio_server_interceptor()  # type: ignore[no-untyped-call]
    return [otel, ObservabilityInterceptor(log, instruments)]


class Server:
    def __init__(
        self,
        cfg: Config,
        log: Logger | None = None,
        *,
        registrars: tuple[ServicerRegistrar, ...] = (),
        probes: tuple[Probe, ...] = (),
        instruments: GRPCInstruments | None = None,
    ) -> None:
        self._cfg = cfg
        self._log = log if log is not None else nop()
        self._probes = probes
        instr = instruments if instruments is not None else new_instruments()
        self._health = Health()
        self._server = grpc.aio.server(
            interceptors=build_interceptors(self._log, instr),
            options=[
                ("grpc.max_receive_message_length", cfg.max_recv_bytes),
                ("grpc.max_send_message_length", cfg.max_send_bytes),
            ],
        )
        self._health.register(self._server)
        for reg in registrars:
            reg(self._server)
        self._targets = self._bind()

    @property
    def server(self) -> grpc.aio.Server:
        return self._server

    @property
    def targets(self) -> tuple[str, ...]:
        return tuple(self._targets)

    def _bind(self) -> list[str]:
        targets: list[str] = []
        if self._cfg.uds_path:
            _unlink_stale(self._cfg.uds_path)
            target = f"unix:{self._cfg.uds_path}"
            self._server.add_insecure_port(target)
            targets.append(target)
        if self._cfg.addr:
            self._server.add_insecure_port(self._cfg.addr)
            targets.append(self._cfg.addr)
        if not targets:
            raise ValueError(
                "grpckit: ни GRPC_UDS_PATH, ни GRPC_ADDR не заданы — некуда биндить сервер"
            )
        return targets

    async def start(self) -> None:
        await self._server.start()
        ready = await self._run_probes()
        # Явно выставляем статус в обе стороны: при not-ready Check вернёт
        # NOT_SERVING (внятный сигнал балансеру), а не NOT_FOUND для неуст. сервиса.
        if ready:
            await self._health.set_serving()
        else:
            await self._health.set_not_serving()
        self._log.info("grpc server started", targets=",".join(self._targets), ready=ready)

    async def _run_probes(self) -> bool:
        failures: list[str] = []
        for probe in self._probes:
            try:
                await probe()
            except Exception as exc:
                failures.append(repr(exc))
        if failures:
            self._log.error("grpc readiness probe failed", failures="; ".join(failures))
            return False
        return True

    async def stop(self) -> None:
        await self._health.shutdown()
        await self._server.stop(self._cfg.shutdown_grace)
        if self._cfg.uds_path:
            _unlink_stale(self._cfg.uds_path)
        self._log.info("grpc server stopped")


def _unlink_stale(path: str) -> None:
    # Снять старый UDS-файл: иначе bind падает с EADDRINUSE на перезапуске.
    try:
        if os.path.exists(path):
            os.unlink(path)
    except OSError:
        pass
