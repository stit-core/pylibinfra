"""testkit — testcontainers-обёртки и per-kit тест-хелперы для консьюмеров.

`start_postgres` / `start_nats` / `start_redis` / `start_minio` — async
context-manager'ы: поднимают контейнер на свободный порт, отдают fixture
(DSN/URL + хелперы поверх боевого кита) и гасят контейнер на выходе; под
отсутствующим Docker `require_docker()` пропускает тест. Сабпакеты
`testkit/<name>test` дают in-memory фейки и хелперы для unit-lane без Docker.

Lifecycle контейнеров — через async context-manager'ы (Python-идиома вместо
Go-шного t.Cleanup), поэтому API framework-agnostic: не завязан на pytest.

Импорт top-level имён (start_nats/…) ленивый (PEP 562): тянет testcontainers
только при фактическом обращении — сабпакеты, которым Docker не нужен,
импортируются без зависимости [testkit].
"""

from typing import TYPE_CHECKING, Any

from ._docker import (
    MINIO_IMAGE,
    NATS_IMAGE,
    POSTGRES_IMAGE,
    REDIS_IMAGE,
    docker_available,
    require_docker,
)

if TYPE_CHECKING:
    from .minio import MinioFixture, start_minio
    from .nats import NatsFixture, start_nats
    from .postgres import PostgresFixture, start_postgres
    from .redis import RedisFixture, start_redis

__all__ = [
    "MINIO_IMAGE",
    "NATS_IMAGE",
    "POSTGRES_IMAGE",
    "REDIS_IMAGE",
    "MinioFixture",
    "NatsFixture",
    "PostgresFixture",
    "RedisFixture",
    "docker_available",
    "require_docker",
    "start_minio",
    "start_nats",
    "start_postgres",
    "start_redis",
]

# имя → относительный модуль (testcontainers-импорт откладывается до обращения)
_LAZY = {
    "MinioFixture": ".minio",
    "start_minio": ".minio",
    "NatsFixture": ".nats",
    "start_nats": ".nats",
    "PostgresFixture": ".postgres",
    "start_postgres": ".postgres",
    "RedisFixture": ".redis",
    "start_redis": ".redis",
}


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module, __name__), name)
