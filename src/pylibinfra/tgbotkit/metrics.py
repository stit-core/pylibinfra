"""Инструменты кита: RED-метрики апдейта, отбраковка апдейтов и исходы платежей."""

from __future__ import annotations

from dataclasses import dataclass

from opentelemetry.metrics import Counter, Histogram

from pylibinfra._internal import otelkit


@dataclass(frozen=True, slots=True)
class Instruments:
    """Набор инструментов кита; создаётся вместе с объектом, а не на уровне модуля."""

    updates: Counter
    duration: Histogram
    panics: Counter
    deduped: Counter
    throttled: Counter
    payments: Counter
    redis_errors: Counter


def new_instruments() -> Instruments:
    """Собирает инструменты на meter'е `pylibinfra.tgbotkit` (no-op без MeterProvider)."""
    meter = otelkit.meter("tgbotkit")
    return Instruments(
        updates=meter.create_counter(
            "tg_updates_total",
            description="Telegram updates processed, by type and result",
        ),
        duration=otelkit.duration_histogram(
            meter,
            "tg_update_duration_seconds",
            "Telegram update processing duration",
        ),
        panics=meter.create_counter(
            "tg_update_panics_total",
            description="Unhandled handler exceptions recovered by the kit",
        ),
        deduped=meter.create_counter(
            "tg_updates_deduped_total",
            description="Updates dropped as duplicates of an already handled update_id",
        ),
        throttled=meter.create_counter(
            "tg_updates_throttled_total",
            description="Updates dropped by the per-user rate limit",
        ),
        payments=meter.create_counter(
            "tg_payments_total",
            description="Payment outcomes by stage and result",
        ),
        redis_errors=meter.create_counter(
            "tg_redis_errors_total",
            description="Redis operations the kit degraded open on, by operation",
        ),
    )
