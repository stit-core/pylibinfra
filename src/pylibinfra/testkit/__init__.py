"""testkit — testcontainers-обёртки и per-kit тест-хелперы для консьюмеров.

start_postgres/start_redis/start_nats — async context-manager'ы: поднимают
контейнер на свободный порт, отдают fixture (DSN/URL + хелпер на боевой kit) и
гасят контейнер на выходе. Подпакеты (audittest, authntest, bruteforcetest,
eventbus, handlertest, txkit) дают per-kit'овые in-memory фейки и ассерты для
unit-lane без Docker.

Lifecycle контейнеров — через async context-manager'ы (Python-идиома вместо
Go-шного t.Cleanup), поэтому API framework-agnostic: не завязан на pytest.
"""

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
