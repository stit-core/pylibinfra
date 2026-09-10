"""start_redis — контейнер Redis для интеграционных тестов и хелпер `cache()` поверх кита."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass

from testcontainers.redis import RedisContainer  # type: ignore[import-untyped]

from pylibinfra.cachekit import Config, RedisCache, new_cache

from ._docker import REDIS_IMAGE, quiet_testcontainers, require_docker

_PORT = 6379


@dataclass(frozen=True, slots=True)
class RedisFixture:
    """Живой Redis теста: клиентский `url` и handle контейнера."""

    url: str
    container: RedisContainer

    def config(self, *, key_prefix: str = "") -> Config:
        """`cachekit.Config` на контейнер; поля заданы явно — `REDIS_*` окружения не влияют."""
        return Config.model_validate(
            {
                "REDIS_URL": self.url,
                "REDIS_KEY_PREFIX": key_prefix,
                "REDIS_POOL_SIZE": 5,
                "REDIS_MAX_RETRIES": 3,
                "REDIS_DIAL_TIMEOUT": 5.0,
                "REDIS_READ_TIMEOUT": 3.0,
                "REDIS_WRITE_TIMEOUT": 3.0,
                "REDIS_PING_ON_START": True,
                "REDIS_SKIP_VERSION_CHECK": False,
            }
        )

    @contextlib.asynccontextmanager
    async def cache(self, *, key_prefix: str = "") -> AsyncIterator[RedisCache]:
        """Открывает `cachekit.RedisCache` на `url`; клиент закрывается на выходе."""
        cache = new_cache(self.config(key_prefix=key_prefix))
        try:
            yield cache
        finally:
            await cache.aclose()


@contextlib.asynccontextmanager
async def start_redis(*, image: str = REDIS_IMAGE) -> AsyncIterator[RedisFixture]:
    """Поднимает Redis (`image`), отдаёт `RedisFixture`, гасит контейнер на выходе."""
    require_docker()
    quiet_testcontainers()
    container = RedisContainer(image)
    try:
        # start() внутри try: сорвавшееся ожидание готовности оставляет контейнер
        # запущенным, и снять его может только finally.
        container.start()
        host = container.get_container_host_ip()
        port = container.get_exposed_port(_PORT)
        yield RedisFixture(url=f"redis://{host}:{port}/0", container=container)
    finally:
        with contextlib.suppress(Exception):
            container.stop()
