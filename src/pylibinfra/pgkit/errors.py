"""Маппинг исключений asyncpg в стабильные предикаты по SQLSTATE-кодам.

asyncpg уже даёт типизированные подклассы (UniqueViolationError и т.д.), но
проверка по коду устойчивее: один предикат покрывает и обёрнутые, и неизвестные
asyncpg-классы с тем же sqlstate.
"""

import asyncpg  # type: ignore[import-untyped]

CODE_UNIQUE_VIOLATION = "23505"
CODE_FOREIGN_KEY_VIOLATION = "23503"
CODE_CHECK_VIOLATION = "23514"
CODE_NOT_NULL_VIOLATION = "23502"
CODE_SERIALIZATION_FAILURE = "40001"
CODE_DEADLOCK_DETECTED = "40P01"


def _pg_err_code(err: BaseException | None) -> str:
    if isinstance(err, asyncpg.PostgresError):
        return err.sqlstate or ""
    return ""


# asyncpg возвращает None из fetchrow при пустом результате (не исключение);
# исключение бывает только из PL/pgSQL (SELECT INTO STRICT) — NoDataFoundError.
def is_no_rows(err: BaseException | None) -> bool:
    return isinstance(err, asyncpg.exceptions.NoDataFoundError)


def is_unique_violation(err: BaseException | None) -> bool:
    return _pg_err_code(err) == CODE_UNIQUE_VIOLATION


def is_foreign_key_violation(err: BaseException | None) -> bool:
    return _pg_err_code(err) == CODE_FOREIGN_KEY_VIOLATION


def is_check_violation(err: BaseException | None) -> bool:
    return _pg_err_code(err) == CODE_CHECK_VIOLATION


def is_not_null_violation(err: BaseException | None) -> bool:
    return _pg_err_code(err) == CODE_NOT_NULL_VIOLATION


def is_retryable(err: BaseException | None) -> bool:
    code = _pg_err_code(err)
    return code in (CODE_SERIALIZATION_FAILURE, CODE_DEADLOCK_DETECTED)
