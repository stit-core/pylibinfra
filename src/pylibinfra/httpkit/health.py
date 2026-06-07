"""Probe Protocol + register_health: /health (liveness) и /ready (readiness).
/ready гоняет переданные probe'ы; любая ошибка → 503 unhealthy.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from fastapi import FastAPI

from .error import error


@runtime_checkable
class Probe(Protocol):
    async def __call__(self) -> None: ...


def register_health(app: FastAPI, *probes: Probe) -> None:
    @app.get("/health", include_in_schema=True, summary="Health check")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", include_in_schema=True, summary="Readiness check")
    async def ready() -> dict[str, str]:
        failures: list[str] = []
        for probe in probes:
            try:
                await probe()
            except Exception as exc:
                failures.append(repr(exc))
        if failures:
            raise error("UNHEALTHY", 503, "; ".join(failures))
        return {"status": "ok"}
