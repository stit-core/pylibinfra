"""Unit-тесты Store и cleanup-job без Postgres."""

from __future__ import annotations

from typing import Any, cast

import pytest

from pylibinfra.bruteforcekit import Cleaner, Config, ConfigError, Guard, InvalidKeyError
from pylibinfra.bruteforcekit.pgstore import MIGRATIONS, BruteforceError, Store, cleanup_job
from pylibinfra.bruteforcekit.pgstore.store import _rows_affected
from pylibinfra.pgkit import DB

_CFG = Config.model_validate({"max_failures": 3, "cleanup_interval": 42.0})


class RefusingDB:
    """DB, который падает на любом запросе: доказывает, что ключ отвергается до похода в базу."""

    async def execute(self, sql: str, *args: Any) -> str:
        raise AssertionError("query must not be executed")

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        raise AssertionError("query must not be executed")


def _store(cfg: Config = _CFG) -> Store:
    return Store(cast(DB, RefusingDB()), cfg)


def test_store_satisfies_both_ports() -> None:
    store = _store()
    assert isinstance(store, Guard)
    assert isinstance(store, Cleaner)


def test_store_rejects_invalid_config() -> None:
    with pytest.raises(ConfigError, match="BRUTEFORCE_MAX_FAILURES"):
        Store(cast(DB, RefusingDB()), Config.model_construct(max_failures=0))


@pytest.mark.parametrize(("subject", "ip"), [("", "1.2.3.4"), ("u", ""), ("u", "1.2.3.4:80")])
async def test_invalid_key_is_rejected_before_query(subject: str, ip: str) -> None:
    store = _store()
    with pytest.raises(InvalidKeyError):
        await store.check(subject, ip)
    with pytest.raises(InvalidKeyError):
        await store.record_failure(subject, ip)
    with pytest.raises(InvalidKeyError):
        await store.record_success(subject, ip)


class BrokenDB:
    """DB, отдающий ошибку соединения на каждом запросе."""

    async def execute(self, sql: str, *args: Any) -> str:
        raise RuntimeError("connection lost")

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        raise RuntimeError("connection lost")


class EmptyDB:
    """DB, у которого RETURNING не отдал строки — редкий, но возможный исход апсерта."""

    async def execute(self, sql: str, *args: Any) -> str:
        return "DELETE 0"

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        return None


@pytest.mark.parametrize(
    ("method", "message"),
    [
        ("check", "bruteforcekit: check"),
        ("record_failure", "bruteforcekit: record failure"),
        ("record_success", "bruteforcekit: record success"),
    ],
)
async def test_database_errors_are_prefixed(method: str, message: str) -> None:
    store = Store(cast(DB, BrokenDB()), _CFG)
    with pytest.raises(BruteforceError, match=message):
        await getattr(store, method)("u@example.com", "1.2.3.4")


async def test_cleanup_error_is_prefixed() -> None:
    store = Store(cast(DB, BrokenDB()), _CFG)
    with pytest.raises(BruteforceError, match="bruteforcekit: cleanup"):
        await store.cleanup()


async def test_missing_returning_row_fails_closed() -> None:
    store = Store(cast(DB, EmptyDB()), _CFG)
    # Пустой RETURNING — не «попыток ещё есть»: неучтённая неудача никогда не
    # доведёт пару до блокировки, поэтому это ошибка.
    with pytest.raises(BruteforceError, match="upsert returned no row"):
        await store.record_failure("u@example.com", "1.2.3.4")
    assert await store.check("u@example.com", "1.2.3.4") is None


@pytest.mark.parametrize(
    ("tag", "rows"),
    [("DELETE 42", 42), ("DELETE 0", 0), ("INSERT 0 1", 1), ("weird", 0), ("", 0)],
)
def test_rows_affected(tag: str, rows: int) -> None:
    assert _rows_affected(tag) == rows


def test_cleanup_job_uses_config_interval() -> None:
    job = cleanup_job(_store(), _CFG)
    assert job.name == "bruteforcekit.cleanup"
    assert job.interval == 42.0
    assert job.timeout == 42.0


def test_migrations_source_is_named_after_kit() -> None:
    assert MIGRATIONS.name == "bruteforcekit"
    assert MIGRATIONS.version_table() == "schema_migrations_bruteforcekit"
    MIGRATIONS.validate()
