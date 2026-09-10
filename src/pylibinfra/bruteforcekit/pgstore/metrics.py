"""Метрики guard'а: исходы проверок, переходы в блокировку и удалённые строки.

Ошибки ключа и базы в счётчики не попадают — их видно по ошибкам вызывающего кода.
"""

from __future__ import annotations

from pylibinfra._internal import otelkit

RESULT_ALLOWED = "allowed"
RESULT_LOCKED = "locked"


class Metrics:
    """Инструменты кита; создаются в конструкторе Store, чтобы тест видел свой MeterProvider."""

    def __init__(self) -> None:
        meter = otelkit.meter("bruteforcekit")
        self.checks = meter.create_counter(
            "bruteforce_checks_total",
            description="Guard checks by result (allowed|locked)",
        )
        self.locks = meter.create_counter(
            "bruteforce_locks_total",
            description="Transitions into the locked state (no active lock -> active lock)",
        )
        self.deleted = meter.create_counter(
            "bruteforce_cleanup_deleted_total",
            description="Rows deleted by cleanup",
        )

    def check(self, result: str) -> None:
        """Учитывает исход `check`."""
        self.checks.add(1, {"result": result})

    def lock(self) -> None:
        """Учитывает переход пары в заблокированное состояние; продление окна не считается."""
        self.locks.add(1)

    def cleanup_deleted(self, rows: int) -> None:
        """Учитывает строки, удалённые одним батчем `cleanup`."""
        if rows:
            self.deleted.add(rows)
