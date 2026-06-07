"""Integration-тесты PgStore против реального Postgres (testcontainers).
Не гоняются в волновом gate — @pytest.mark.integration (нужен Docker).
Зеркалят сценарии golibinfra/bruteforcekit/pgstore/store_test.go.
"""

from collections.abc import AsyncIterator, Iterator

import pytest
from testcontainers.postgres import PostgresContainer

from pylibinfra.bruteforcekit import Config, Guard
from pylibinfra.bruteforcekit.pgstore import PgStore, migrations_dir
from pylibinfra.clockkit import Real
from pylibinfra.pgkit import DB, MigrateConfig, PoolConfig, migrate_up, new_pool

pytestmark = pytest.mark.integration

_CFG = Config(max_failures=3, lock_duration=900.0, window=3600.0, cleanup_retention=0.0)


@pytest.fixture(scope="module")
def pg_dsn() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine") as pg:
        host = pg.get_container_host_ip()
        port = pg.get_exposed_port(5432)
        dsn = f"postgresql://{pg.username}:{pg.password}@{host}:{port}/{pg.dbname}"
        migrate_up(MigrateConfig(dsn=dsn, path=migrations_dir()))
        yield dsn


@pytest.fixture
async def guard(pg_dsn: str) -> AsyncIterator[Guard]:
    pool = await new_pool(PoolConfig(dsn=pg_dsn, min_conns=1, max_conns=3))
    try:
        await pool.execute("TRUNCATE bruteforce_attempts")
        yield Guard(PgStore(DB(pool), _CFG), Real())
    finally:
        await pool.close()


async def test_lock_after_max_failures(guard: Guard) -> None:
    subject, ip = "user@example.com", "10.0.0.1"
    assert await guard.check(subject, ip) is None

    for _ in range(_CFG.max_failures - 1):
        await guard.record_failure(subject, ip)
    assert await guard.check(subject, ip) is None

    await guard.record_failure(subject, ip)
    state = await guard.check(subject, ip)
    assert state is not None


async def test_record_success_resets(guard: Guard) -> None:
    subject, ip = "phone:+15555550123", "10.0.0.2"
    for _ in range(_CFG.max_failures - 1):
        await guard.record_failure(subject, ip)
    await guard.record_success(subject, ip)
    assert await guard.check(subject, ip) is None

    # Счётчик с нуля: одна неудача после reset не блокирует.
    await guard.record_failure(subject, ip)
    assert await guard.check(subject, ip) is None


async def test_cleanup_keeps_locked(guard: Guard) -> None:
    await guard.record_failure("stale@example.com", "1.1.1.1")
    for _ in range(_CFG.max_failures):
        await guard.record_failure("locked@example.com", "2.2.2.2")

    await guard.cleanup()

    assert await guard.check("stale@example.com", "1.1.1.1") is None
    assert await guard.check("locked@example.com", "2.2.2.2") is not None


async def test_failures_outside_window_reset_counter(pg_dsn: str) -> None:
    # Реальное NOW() БД нельзя сдвинуть, поэтому состариваем last_attempt руками,
    # имитируя неудачи за пределами окна — проверяем сброс счётчика.
    pool = await new_pool(PoolConfig(dsn=pg_dsn, min_conns=1, max_conns=3))
    try:
        await pool.execute("TRUNCATE bruteforce_attempts")
        guard = Guard(PgStore(DB(pool), _CFG), Real())
        subject, ip = "windowed@example.com", "10.0.0.9"

        for _ in range(_CFG.max_failures - 1):
            await guard.record_failure(subject, ip)
        # Сдвигаем последнюю попытку за окно — прежние неудачи «протухли».
        await pool.execute(
            "UPDATE bruteforce_attempts SET last_attempt = NOW() - make_interval(secs => $1) "
            "WHERE subject = $2 AND ip = $3",
            int(_CFG.window) + 60,
            subject,
            ip,
        )

        # Новая неудача стартует счётчик заново → не блокирует.
        await guard.record_failure(subject, ip)
        assert await guard.check(subject, ip) is None

        count = await pool.fetchval(
            "SELECT failed_count FROM bruteforce_attempts WHERE subject = $1 AND ip = $2",
            subject,
            ip,
        )
        assert count == 1
    finally:
        await pool.close()
