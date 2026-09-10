"""`/health` (liveness) и `/ready` (readiness по пробам healthkit)."""

from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from pylibinfra import healthkit
from pylibinfra.logkit import Logger, nop

Probe = healthkit.Probe

HEALTH_PATH = "/health"
READY_PATH = "/ready"


async def _status(probe: healthkit.Probe, seconds: float, log: Logger) -> dict[str, str]:
    """Статус одной пробы; текст ошибки уходит только в лог — в нём бывают DSN и адреса."""
    try:
        await healthkit.with_timeout(probe, seconds).run()
    except Exception as exc:
        log.warn("readiness probe failed", probe=probe.name, err=repr(exc))
        return {"name": probe.name, "status": "down"}
    return {"name": probe.name, "status": "ok"}


async def run_probes(probes: healthkit.Probes, seconds: float, log: Logger) -> list[dict[str, str]]:
    """Гоняет пробы параллельно: латентность `/ready` — максимум по пробам, а не сумма."""
    if not probes:
        return []
    return list(await asyncio.gather(*(_status(p, seconds, log) for p in probes)))


def register_health(
    app: FastAPI,
    log: Logger | None = None,
    probe_timeout: float = 5.0,
    probes: healthkit.Probes = (),
) -> None:
    """Регистрирует `/health` и `/ready`; любая упавшая проба даёт 503 `unhealthy`."""
    logger = log if log is not None else nop()

    @app.get(HEALTH_PATH, summary="Health check")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(READY_PATH, summary="Readiness check")
    async def ready() -> JSONResponse:
        statuses = await run_probes(probes, probe_timeout, logger)
        down = any(item["status"] != "ok" for item in statuses)
        body: dict[str, Any] = {"status": "unhealthy" if down else "ok"}
        if statuses:
            body["probes"] = statuses
        code = HTTPStatus.SERVICE_UNAVAILABLE if down else HTTPStatus.OK
        return JSONResponse(status_code=int(code), content=body)
