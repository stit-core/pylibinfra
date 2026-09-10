"""FastAPI-lifespan, закрывающий dishka-контейнер после того, как сервер отдал все ответы."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from dishka import AsyncContainer
from fastapi import FastAPI

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def dishka_lifespan(container: AsyncContainer) -> Lifespan:
    """Lifespan, закрывающий контейнер (telemetry-flush, redis/nats/pg) после остановки сервера."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await container.close()

    return lifespan
