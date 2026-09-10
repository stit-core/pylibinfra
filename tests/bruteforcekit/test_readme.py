"""Сниппет быстрого старта из README кита — закреплён рабочим прогоном."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

import pytest
import pytest_asyncio
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import clockkit, timerkit
from pylibinfra.bruteforcekit import Config, Guard, new_config
from pylibinfra.bruteforcekit.pgstore import (
    MIGRATIONS,
    BruteforcePgstoreProvider,
    Store,
    cleanup_job,
)
from pylibinfra.logkit import Logger, nop
from pylibinfra.pgkit import (
    Migrated,
    MigrateRunnerConfig,
    MigrationSources,
    PgMigrateProvider,
    PgProvider,
    PoolConfig,
    new_migrate_runner_config,
    new_pool_config,
)
from pylibinfra.testkit import PostgresFixture, start_postgres

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


class LockedError(Exception):
    def __init__(self, until: datetime) -> None:
        super().__init__(f"locked until {until}")


class InvalidCredentialsError(Exception):
    pass


class UserRepo:
    async def check_password(self, email: str, password: str) -> bool:
        return password == "correct"


class AuthService:
    def __init__(self, guard: Guard, users: UserRepo) -> None:
        self._guard = guard
        self._users = users

    async def login(self, email: str, ip: str, password: str) -> None:
        lock = await self._guard.check(email, ip)
        if lock is not None:
            raise LockedError(lock.until)  # отдать 429 / 423

        if not await self._users.check_password(email, password):
            lock = await self._guard.record_failure(email, ip)
            if lock is not None:
                raise LockedError(lock.until)
            raise InvalidCredentialsError

        await self._guard.record_success(email, ip)  # успех сбрасывает счётчик


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def pool_config(self) -> PoolConfig:
        return new_pool_config()

    @provide
    def migrate_config(self) -> MigrateRunnerConfig:
        return new_migrate_runner_config()

    @provide
    def bruteforce_config(self) -> Config:
        return new_config()  # BRUTEFORCE_*

    @provide
    def migration_sources(self) -> MigrationSources:
        return (MIGRATIONS,)

    @provide
    def jobs(self, store: Store, cfg: Config) -> timerkit.Jobs:
        return (cleanup_job(store, cfg),)  # без него таблица не чистится

    @provide
    def logger(self) -> Logger:
        # В приложении — LogkitProvider из README; здесь nop(), чтобы тест не
        # уводил корневой logging в свой логгер на весь прогон.
        return nop()

    @provide
    def users(self) -> UserRepo:
        return UserRepo()

    @provide
    def auth_service(self, guard: Guard, users: UserRepo) -> AuthService:
        return AuthService(guard, users)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def pg() -> AsyncIterator[PostgresFixture]:
    async with start_postgres() as fixture:
        yield fixture


async def test_readme_quickstart(pg: PostgresFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_DSN", pg.dsn)
    monkeypatch.setenv("PG_MIGRATIONS_PATH", "")
    monkeypatch.setenv("BRUTEFORCE_MAX_FAILURES", "2")

    container = make_async_container(
        PgProvider(),
        PgMigrateProvider(),
        clockkit.ClockProvider(),
        timerkit.TimerkitProvider(),
        BruteforcePgstoreProvider(),
        AppProvider(),
    )
    try:
        await container.get(Migrated)
        auth = await container.get(AuthService)

        with pytest.raises(InvalidCredentialsError):
            await auth.login("user@example.com", "203.0.113.7", "wrong")
        with pytest.raises(LockedError):
            await auth.login("user@example.com", "203.0.113.7", "wrong")
        with pytest.raises(LockedError):
            await auth.login("user@example.com", "203.0.113.7", "correct")

        jobs = await container.get(timerkit.Jobs)
        assert [job.name for job in jobs] == ["bruteforcekit.cleanup"]
    finally:
        await container.close()
