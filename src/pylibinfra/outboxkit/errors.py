"""Ошибки outboxkit: невалидный конфиг, занятая очередь, insert вне транзакции."""

from __future__ import annotations

from pylibinfra._internal import configkit


class OutboxError(Exception):
    """База ошибок кита (кроме `ConfigError` — та наследует `ValueError`)."""


class ConfigError(configkit.ConfigError):
    """Невалидный конфиг кита или его pgstore: валит старт сервиса."""


class BusyError(OutboxError):
    """Ordered-режим: голову очереди держит другой воркер — тик пропускается."""

    def __init__(self) -> None:
        super().__init__("outboxkit: queue head is held by another worker")


class NoTxError(OutboxError):
    """`insert` вызван вне транзакции при включённом `OUTBOX_REQUIRE_TX`."""

    def __init__(self) -> None:
        super().__init__(
            "outboxkit: insert outside transaction: wrap the call in pgkit Transactor.with_tx"
        )


class NotSupportedError(OutboxError):
    """Операция не поддержана реализацией `Repository` (например `revive_dead`)."""
