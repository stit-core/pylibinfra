import asyncpg
import pytest

from pylibinfra.pgkit import (
    is_check_violation,
    is_foreign_key_violation,
    is_no_rows,
    is_not_null_violation,
    is_retryable,
    is_unique_violation,
)

_PREDICATES = [
    is_unique_violation,
    is_foreign_key_violation,
    is_check_violation,
    is_not_null_violation,
    is_retryable,
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
    ],
)
def test_predicate_matches_only_its_class(exc, predicate) -> None:
    err = exc("boom")
    assert predicate(err)
    for other in _PREDICATES:
        if other is predicate:
            continue
        assert not other(err)


def test_predicates_match_wrapped_error() -> None:
    err = RuntimeError("layer")
    err.__cause__ = asyncpg.UniqueViolationError("dup")
    # обёртка не PostgresError — предикат смотрит на сам объект, не на cause
    assert not is_unique_violation(err)
    assert is_unique_violation(asyncpg.UniqueViolationError("dup"))


def test_is_no_rows() -> None:
    assert is_no_rows(asyncpg.exceptions.NoDataFoundError("nd"))
    assert not is_no_rows(None)
    assert not is_no_rows(asyncpg.UniqueViolationError("dup"))


def test_predicates_false_on_nil() -> None:
    for predicate in _PREDICATES:
        assert not predicate(None)
        assert not predicate(ValueError("not pg"))
