"""Интеграционные тесты bruteforcekit/pgstore против реального Postgres."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest
import pytest_asyncio
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.bruteforcekit import Cleaner, Config, Guard
from pylibinfra.bruteforcekit.pgstore import (
    MIGRATIONS,
    BruteforcePgstoreProvider,
    Store,
    cleanup_job,
)
from pylibinfra.pgkit import DB, migrate_up_all
from pylibinfra.testkit import PostgresFixture, start_postgres

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_CFG = Config.model_validate(
    {"max_failures": 3, "lock_duration": 900.0, "cleanup_retention": 0.0, "ipv6_prefix": 64}
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pg() -> AsyncIterator[PostgresFixture]:
    async with start_postgres(migrations=[MIGRATIONS]) as fixture:
        yield fixture


@pytest_asyncio.fixture(loop_scope="module")
async def db(pg: PostgresFixture) -> DB:
    return await pg.db()


@pytest_asyncio.fixture(loop_scope="module")
async def store(db: DB) -> Store:
    return Store(db, _CFG)


async def test_lock_after_max_failures(store: Store) -> None:
    subject, ip = "lock@example.com", "10.0.0.1"
    assert await store.check(subject, ip) is None

    for _ in range(_CFG.max_failures - 1):
        assert await store.record_failure(subject, ip) is None
    assert await store.check(subject, ip) is None

    state = await store.record_failure(subject, ip)
    assert state is not None
    assert state.failed_count == _CFG.max_failures

    checked = await store.check(subject, ip)
    assert checked is not None
    assert checked.until == state.until


async def test_threshold_one_locks_from_first_failure(db: DB) -> None:
    store = Store(db, _CFG.model_copy(update={"max_failures": 1}))
    assert await store.record_failure("first@example.com", "10.0.0.2") is not None


async def test_buckets_are_independent(store: Store) -> None:
    for _ in range(_CFG.max_failures):
        await store.record_failure("alice@example.com", "10.0.1.1")

    assert await store.check("alice@example.com", "10.0.1.1") is not None
    assert await store.check("alice@example.com", "10.0.1.2") is None
    assert await store.check("bob@example.com", "10.0.1.1") is None


async def test_record_success_clears_history(store: Store) -> None:
    subject, ip = "success@example.com", "10.0.2.1"
    for _ in range(_CFG.max_failures - 1):
        await store.record_failure(subject, ip)
    await store.record_success(subject, ip)

    assert await store.check(subject, ip) is None
    assert await store.record_failure(subject, ip) is None


async def test_expired_lock_starts_new_window(db: DB, store: Store) -> None:
    subject, ip = "window@example.com", "10.0.3.1"
    for _ in range(_CFG.max_failures):
        await store.record_failure(subject, ip)
    await _expire_lock(db, subject, ip)

    assert await store.check(subject, ip) is None
    assert await store.record_failure(subject, ip) is None
    assert await _failed_count(db, subject, ip) == 1


async def test_raising_threshold_keeps_active_lock(db: DB) -> None:
    subject, ip = "threshold@example.com", "10.0.4.1"
    strict = Store(db, _CFG.model_copy(update={"max_failures": 1}))
    assert await strict.record_failure(subject, ip) is not None

    relaxed = Store(db, _CFG.model_copy(update={"max_failures": 100}))
    assert await relaxed.check(subject, ip) is not None


async def test_check_uses_database_clock(db: DB, store: Store) -> None:
    subject, ip = "clock@example.com", "10.0.5.1"
    for _ in range(_CFG.max_failures):
        await store.record_failure(subject, ip)

    now = await db.fetchval("SELECT NOW()")
    state = await store.check(subject, ip)
    assert state is not None
    assert state.until > now


async def test_ip_is_canonicalized(store: Store) -> None:
    for _ in range(_CFG.max_failures):
        await store.record_failure("canon@example.com", "::ffff:203.0.113.9")

    # Тот же адрес в IPv4-записи — тот же бакет счётчика.
    assert await store.check("canon@example.com", "203.0.113.9") is not None


async def test_ipv6_is_aggregated_by_prefix(store: Store) -> None:
    for _ in range(_CFG.max_failures):
        await store.record_failure("v6@example.com", "2001:db8:0:7::5")

    assert await store.check("v6@example.com", "2001:db8:0:7::99") is not None
    assert await store.check("v6@example.com", "2001:db8:0:8::5") is None


async def test_concurrent_failures_increment_atomically(db: DB) -> None:
    subject, ip = "concurrent@example.com", "10.0.6.1"
    store = Store(db, _CFG.model_copy(update={"max_failures": 100}))

    await asyncio.gather(*(store.record_failure(subject, ip) for _ in range(10)))
    assert await _failed_count(db, subject, ip) == 10


async def test_cleanup_deletes_in_batches(
    db: DB, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    await db.execute("TRUNCATE bruteforce_attempts")
    for n in range(5):
        await store.record_failure(f"batch-{n}@example.com", "10.0.7.1")
    for _ in range(_CFG.max_failures):
        await store.record_failure("kept@example.com", "10.0.7.2")

    monkeypatch.setattr("pylibinfra.bruteforcekit.pgstore.store.CLEANUP_BATCH_SIZE", 2)
    await store.cleanup()

    remaining = await db.fetchval("SELECT count(*) FROM bruteforce_attempts")
    assert remaining == 1
    assert await store.check("kept@example.com", "10.0.7.2") is not None


async def test_cleanup_keeps_rows_within_retention(db: DB) -> None:
    await db.execute("TRUNCATE bruteforce_attempts")
    store = Store(db, _CFG.model_copy(update={"cleanup_retention": 3600.0}))
    await store.record_failure("fresh@example.com", "10.0.8.1")

    await store.cleanup()
    assert await db.fetchval("SELECT count(*) FROM bruteforce_attempts") == 1


async def test_metrics(db: DB, read_metric: Callable[..., float]) -> None:
    await db.execute("TRUNCATE bruteforce_attempts")
    store = Store(db, _CFG)
    subject, ip = "metrics@example.com", "10.0.9.1"

    assert await store.check(subject, ip) is None
    for _ in range(_CFG.max_failures):
        await store.record_failure(subject, ip)
    assert await store.check(subject, ip) is not None
    # Продление активной блокировки — то же окно, счётчик locks не двигается.
    await store.record_failure(subject, ip)

    assert read_metric("bruteforce_checks_total", {"result": "allowed"}) == 1
    assert read_metric("bruteforce_checks_total", {"result": "locked"}) == 1
    assert read_metric("bruteforce_locks_total") == 1

    await _expire_lock(db, subject, ip)
    await store.cleanup()
    assert read_metric("bruteforce_cleanup_deleted_total") == 1


async def test_migrations_are_idempotent(pg: PostgresFixture, db: DB) -> None:
    await asyncio.to_thread(migrate_up_all, pg.dsn, MIGRATIONS, timeout=30.0)
    assert await db.fetchval("SELECT count(*) FROM schema_migrations_bruteforcekit") == 2


async def test_migration_0002_swaps_indexes(db: DB) -> None:
    indexes = [
        r["indexname"]
        for r in await db.fetch(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'bruteforce_attempts'"
        )
    ]
    assert "bruteforce_attempts_last_attempt_idx" in indexes
    assert "bruteforce_attempts_locked_idx" not in indexes
    assert await db.fetchval("SELECT count(*) FROM schema_migrations_bruteforcekit") == 2


async def test_provider_graph_builds(pg: PostgresFixture) -> None:
    pool_db = await pg.db()

    class AppProvider(Provider):
        scope = Scope.APP

        @provide
        def db(self) -> DB:
            return pool_db

        @provide
        def config(self) -> Config:
            return _CFG

    container = make_async_container(BruteforcePgstoreProvider(), AppProvider())
    try:
        guard = await container.get(Guard)
        cleaner = await container.get(Cleaner)
        assert guard is cleaner
        assert await guard.check("di@example.com", "10.0.10.1") is None
        await cleaner.cleanup()
        job = cleanup_job(await container.get(Store), _CFG)
        assert job.name == "bruteforcekit.cleanup"
    finally:
        await container.close()


async def _expire_lock(db: DB, subject: str, ip: str) -> None:
    await db.execute(
        "UPDATE bruteforce_attempts SET locked_until = NOW() - INTERVAL '1 second', "
        "last_attempt = NOW() - INTERVAL '1 second' WHERE subject = $1 AND ip = $2",
        subject,
        ip,
    )


async def _failed_count(db: DB, subject: str, ip: str) -> int:
    return int(
        await db.fetchval(
            "SELECT failed_count FROM bruteforce_attempts WHERE subject = $1 AND ip = $2",
            subject,
            ip,
        )
    )
