"""dishka_lifespan — FastAPI-lifespan, закрывающий dishka-контейнер ПОСЛЕ
остановки сервера. Без него контейнер (а с ним telemetry-flush, redis/nats/pg
aclose) пришлось бы закрывать снаружи, гадая про порядок относительно uvicorn —
lifespan гарантирует «закрыть после того, как сервер отдал все ответы».
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from dishka import AsyncContainer
from fastapi import FastAPI

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def dishka_lifespan(container: AsyncContainer) -> Lifespan:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await container.close()

    return lifespan
