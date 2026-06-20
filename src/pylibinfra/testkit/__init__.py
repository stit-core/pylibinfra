"""testkit — testcontainers-обёртки и per-kit тест-хелперы для консьюмеров.

start_postgres/start_redis/start_nats — async context-manager'ы: поднимают
контейнер на свободный порт, отдают fixture (DSN/URL + хелпер на боевой kit) и
гасят контейнер на выходе. Подпакеты (audittest, authntest, bruteforcetest,
eventbus, handlertest, txkit, grpctest) дают per-kit'овые in-memory фейки и
хелперы для unit-lane без Docker.

Lifecycle контейнеров — через async context-manager'ы (Python-идиома вместо
Go-шного t.Cleanup), поэтому API framework-agnostic: не завязан на pytest.

Импорт top-level имён (start_nats/…) **ленивый** (PEP 562): тянет testcontainers
только при фактическом обращении — так подпакеты вроде grpctest, которым Docker не
нужен, импортируются без зависимости [testkit].
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .nats import NatsFixture, start_nats
    from .postgres import PostgresFixture, start_postgres
    from .redis import RedisFixture, start_redis

__all__ = [
    "NatsFixture",
    "PostgresFixture",
    "RedisFixture",
    "start_nats",
    "start_postgres",
    "start_redis",
]

# имя → относительный модуль (testcontainers-импорт откладывается до обращения)
_LAZY = {
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
