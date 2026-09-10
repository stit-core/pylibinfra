"""Метрики записи аудита: счётчик исходов и длительность INSERT'а.

Потребители обычно логируют ошибку `log` и продолжают доменную операцию —
`audit_log_writes_total{result!="ok"}` остаётся единственным сигналом потери записи.
"""

from __future__ import annotations

from pylibinfra._internal import otelkit

RESULT_OK = "ok"
RESULT_ERROR = "error"
RESULT_INVALID = "invalid"


class Metrics:
    """Инструменты кита; создаются в конструкторе Store, чтобы тест видел свой MeterProvider."""

    def __init__(self) -> None:
        meter = otelkit.meter("auditkit")
        self.writes = meter.create_counter(
            "audit_log_writes_total",
            description="Audit log writes by result (ok|error|invalid)",
        )
        self.write_duration = otelkit.duration_histogram(
            meter,
            "audit_log_write_duration_seconds",
            "Audit log INSERT duration",
        )

    def observe_write(self, result: str, seconds: float) -> None:
        """Учитывает исход и длительность INSERT'а."""
        attrs = {"result": result}
        self.writes.add(1, attrs)
        self.write_duration.record(seconds, attrs)

    def invalid(self) -> None:
        """Учитывает запись, отвергнутую до похода в базу (длительности у неё нет)."""
        self.writes.add(1, {"result": RESULT_INVALID})
