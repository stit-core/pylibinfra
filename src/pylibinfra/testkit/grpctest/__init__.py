"""grpctest — helpers для интеграционных тестов gRPC-консьюмеров grpckit.

`running_server(*registrars)` поднимает реальный grpckit.Server на временном UDS
(тот же транспорт, что в проде, без гонок за TCP-порт) и отдаёт target для
grpc.aio.insecure_channel. Стандартный health доступен сразу.
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from pylibinfra.grpckit import Config, Probe, Server, ServicerRegistrar


@asynccontextmanager
async def running_server(
    *registrars: ServicerRegistrar,
    probes: tuple[Probe, ...] = (),
    shutdown_grace: float = 1.0,
) -> AsyncIterator[str]:
    """Запускает grpckit.Server на временном UDS, yield'ит target канала
    (`unix:/tmp/.../x.sock`), гасит на выходе."""
    sock = Path(tempfile.gettempdir()) / f"grpctest-{uuid.uuid4().hex}.sock"
    server = Server(
        Config(uds_path=str(sock), shutdown_grace=shutdown_grace),
        registrars=tuple(registrars),
        probes=probes,
    )
    await server.start()
    try:
        yield f"unix:{sock}"
    finally:
        await server.stop()


__all__ = ["running_server"]
