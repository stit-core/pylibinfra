"""grpctest — харнесс для тестов консьюмеров grpckit.

`running_server(*services)` поднимает настоящий `grpckit.Server` (со всей цепочкой
интерсепторов и health) на временном UDS или на TCP-порту ОС и отдаёт target для
`grpc.aio.insecure_channel`; на выходе сервер гасится.
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from pylibinfra.grpckit import Authenticator, Config, Server, ServiceRegistrar
from pylibinfra.healthkit import Probes
from pylibinfra.logkit import Logger

UDS = "uds"
TCP = "tcp"
# Тесту не нужен полный грейс: висящих стримов в нём обычно нет, а выход должен быть быстрым.
SHUTDOWN_GRACE = 1.0

# Хосты «все интерфейсы»: слушателю они годятся, каналу — нет.
_WILDCARD_HOSTS = ("", "[::]", "0.0.0.0", "::")  # noqa: S104


@asynccontextmanager
async def running_server(
    *services: ServiceRegistrar,
    probes: Probes = (),
    authenticator: Authenticator | None = None,
    interceptors: Sequence[Any] = (),
    log: Logger | None = None,
    transport: str = UDS,
    config: Config | None = None,
) -> AsyncIterator[str]:
    """Запускает сервер и yield'ит target канала; `config` перекрывает транспорт целиком."""
    cfg = config if config is not None else new_config(transport)
    server = Server(
        cfg,
        log,
        services=tuple(services),
        probes=probes,
        authenticator=authenticator,
        interceptors=interceptors,
    )
    await server.serve()
    try:
        yield target(cfg, server)
    finally:
        await server.stop()


def new_config(transport: str = UDS) -> Config:
    """Конфиг тестового сервера: UDS во временном файле либо TCP на порту ОС."""
    # model_validate, а не Config(...): поля заполняются по именам, а адреса и
    # грейс теста не подмешиваются из ENV прогона.
    if transport == TCP:
        return Config.model_validate({"addr": "127.0.0.1:0", "shutdown_grace": SHUTDOWN_GRACE})
    if transport == UDS:
        sock = Path(tempfile.gettempdir()) / f"grpctest-{uuid.uuid4().hex}.sock"
        return Config.model_validate(
            {"addr": "", "uds_path": str(sock), "shutdown_grace": SHUTDOWN_GRACE}
        )
    raise ValueError(f"grpctest: unknown transport {transport!r}: want {UDS!r} or {TCP!r}")


def target(cfg: Config, server: Server) -> str:
    """Target канала для запущенного сервера: UDS приоритетнее TCP."""
    if cfg.uds_path:
        return f"unix:{cfg.uds_path}"
    host, _, port = server.addr().rpartition(":")
    # Слушатель всех интерфейсов дозванивается через loopback.
    return f"127.0.0.1:{port}" if host in _WILDCARD_HOSTS else f"{host}:{port}"


__all__ = ["SHUTDOWN_GRACE", "TCP", "UDS", "new_config", "running_server", "target"]
