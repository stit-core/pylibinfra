"""Исключения кита и предикаты pg-ошибок по SQLSTATE-кодам.

asyncpg даёт типизированные подклассы (UniqueViolationError и т.д.), но проверка
по коду устойчивее: один предикат покрывает и неизвестные asyncpg-классы с тем же
sqlstate, и ошибку, обёрнутую слоем выше (предикат идёт по цепочке `__cause__`).
"""

from __future__ import annotations

import asyncpg  # type: ignore[import-untyped]

from pylibinfra._internal import configkit

CODE_UNIQUE_VIOLATION = "23505"
CODE_FOREIGN_KEY_VIOLATION = "23503"
CODE_CHECK_VIOLATION = "23514"
CODE_NOT_NULL_VIOLATION = "23502"
CODE_SERIALIZATION_FAILURE = "40001"
CODE_DEADLOCK_DETECTED = "40P01"
CODE_LOCK_NOT_AVAILABLE = "55P03"
CODE_QUERY_CANCELED = "57014"

# Глубина раскрутки `__cause__`: аналог errors.As в Go, но без риска зациклиться
# на самодельной цепочке исключений.
_MAX_CAUSE_DEPTH = 16


class PgkitError(Exception):
    """База ошибок pgkit: пул, транзакции, миграции, разбор UUID."""


class ConfigError(configkit.ConfigError, PgkitError):
    """Невалидный конфиг кита (`PoolConfig`/`ReplicaConfig`/`MigrateRunnerConfig`)."""


class ConnectError(PgkitError):
    """Пул не открылся или не прошёл стартовый ping."""


class TransactionError(PgkitError):
    """Не удалось открыть, зафиксировать или откатить транзакцию."""


class MigrateError(PgkitError):
    """Прогон миграций сорвался: невалидный источник, занятый advisory-lock, ошибка SQL."""


class InvalidUUIDError(PgkitError, ValueError):
    """Строка на входе `parse_uuid` пуста или не разбирается как UUID."""


def _pg_err_code(err: BaseException | None) -> str:
    """SQLSTATE ошибки или её причины; пустая строка для не-pg ошибок."""
    seen = err
    for _ in range(_MAX_CAUSE_DEPTH):
        if seen is None:
            return ""
        if isinstance(seen, asyncpg.PostgresError):
            return str(seen.sqlstate or "")
        seen = seen.__cause__
    return ""


def is_no_rows(err: BaseException | None) -> bool:
    """Запрос не вернул строк там, где строка обязательна (PL/pgSQL `SELECT INTO STRICT`)."""
    seen = err
    for _ in range(_MAX_CAUSE_DEPTH):
        if seen is None:
            return False
        if isinstance(seen, asyncpg.exceptions.NoDataFoundError):
            return True
        seen = seen.__cause__
    return False


def is_unique_violation(err: BaseException | None) -> bool:
    """23505 — нарушение unique-ограничения."""
    return _pg_err_code(err) == CODE_UNIQUE_VIOLATION


def is_foreign_key_violation(err: BaseException | None) -> bool:
    """23503 — нарушение внешнего ключа."""
    return _pg_err_code(err) == CODE_FOREIGN_KEY_VIOLATION


def is_check_violation(err: BaseException | None) -> bool:
    """23514 — нарушение CHECK-ограничения."""
    return _pg_err_code(err) == CODE_CHECK_VIOLATION


def is_not_null_violation(err: BaseException | None) -> bool:
    """23502 — NULL в NOT NULL-колонке."""
    return _pg_err_code(err) == CODE_NOT_NULL_VIOLATION


def is_lock_not_available(err: BaseException | None) -> bool:
    """55P03 — `NOWAIT` или `lock_timeout` не дождались блокировки."""
    return _pg_err_code(err) == CODE_LOCK_NOT_AVAILABLE


def is_query_canceled(err: BaseException | None) -> bool:
    """57014 — `statement_timeout` или `pg_cancel_backend` прервали запрос."""
    return _pg_err_code(err) == CODE_QUERY_CANCELED


def is_retryable(err: BaseException | None) -> bool:
    """40001 (serialization failure) или 40P01 (deadlock): транзиентный конфликт, повтор уместен."""
    code = _pg_err_code(err)
    return code in (CODE_SERIALIZATION_FAILURE, CODE_DEADLOCK_DETECTED)
