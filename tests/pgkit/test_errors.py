import asyncpg
import pytest

from pylibinfra._internal import configkit
from pylibinfra.pgkit import (
    ConfigError,
    ConnectError,
    InvalidUUIDError,
    MigrateError,
    PgkitError,
    TransactionError,
    is_check_violation,
    is_foreign_key_violation,
    is_lock_not_available,
    is_no_rows,
    is_not_null_violation,
    is_query_canceled,
    is_retryable,
    is_unique_violation,
)

_PREDICATES = [
    is_unique_violation,
    is_foreign_key_violation,
    is_check_violation,
    is_not_null_violation,
    is_retryable,
    is_lock_not_available,
    is_query_canceled,
]


@pytest.mark.parametrize(
    ("exc", "predicate"),
    [
        (asyncpg.UniqueViolationError, is_unique_violation),
        (asyncpg.ForeignKeyViolationError, is_foreign_key_violation),
        (asyncpg.CheckViolationError, is_check_violation),
        (asyncpg.NotNullViolationError, is_not_null_violation),
        (asyncpg.SerializationError, is_retryable),
        (asyncpg.DeadlockDetectedError, is_retryable),
        (asyncpg.exceptions.LockNotAvailableError, is_lock_not_available),
        (asyncpg.exceptions.QueryCanceledError, is_query_canceled),
    ],
)
def test_predicate_matches_only_its_class(exc, predicate) -> None:
    err = exc("boom")
    assert predicate(err)
    for other in _PREDICATES:
        if other is predicate:
            continue
        assert not other(err)


def test_predicates_follow_cause_chain() -> None:
    wrapped = RuntimeError("repo layer")
    wrapped.__cause__ = asyncpg.UniqueViolationError("dup")
    assert is_unique_violation(wrapped)
    assert not is_check_violation(wrapped)


def test_predicates_follow_nested_causes() -> None:
    outer = RuntimeError("service layer")
    outer.__cause__ = RuntimeError("repo layer")
    outer.__cause__.__cause__ = asyncpg.exceptions.NoDataFoundError("nd")
    assert is_no_rows(outer)


def test_predicates_stop_on_cycle() -> None:
    err = RuntimeError("a")
    err.__cause__ = err
    assert not is_unique_violation(err)
    assert not is_no_rows(err)


def test_is_no_rows() -> None:
    assert is_no_rows(asyncpg.exceptions.NoDataFoundError("nd"))
    assert not is_no_rows(None)
    assert not is_no_rows(asyncpg.UniqueViolationError("dup"))


def test_predicates_false_on_nil() -> None:
    for predicate in _PREDICATES:
        assert not predicate(None)
        assert not predicate(ValueError("not pg"))


def test_error_hierarchy() -> None:
    for cls in (ConnectError, TransactionError, MigrateError, InvalidUUIDError, ConfigError):
        assert issubclass(cls, PgkitError)
    assert issubclass(ConfigError, configkit.ConfigError)
    assert issubclass(ConfigError, ValueError)
    assert issubclass(InvalidUUIDError, ValueError)
