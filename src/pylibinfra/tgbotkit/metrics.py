"""TgInstruments — OTel-инструменты апдейта/платежей. Берёт глобальный
metrics.get_meter("app") (telemetry.init выставляет provider; без telemetry —
no-op). Имена инфра-метрики (tg_*), не biz_* — как http_requests_total у httpkit.
"""

from opentelemetry import metrics
from opentelemetry.metrics import Counter, Histogram, Meter

_DURATION_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30]


class TgInstruments:
    updates_total: Counter
    update_duration: Histogram
    panics_total: Counter
    deduped_total: Counter
    payments_total: Counter

    def __init__(self, meter: Meter) -> None:
        self.updates_total = meter.create_counter(
            "tg_updates_total",
            description="Total Telegram updates processed, by type and result",
        )
        self.update_duration = meter.create_histogram(
            "tg_update_duration_seconds",
            description="Telegram update processing duration in seconds",
            explicit_bucket_boundaries_advisory=_DURATION_BUCKETS,
        )
        self.panics_total = meter.create_counter(
            "tg_update_panics_total",
            description="Exceptions recovered in the update handler chain",
        )
        self.deduped_total = meter.create_counter(
            "tg_updates_deduped_total",
            description="Updates dropped as duplicates (Telegram at-least-once retries)",
        )
        self.payments_total = meter.create_counter(
            "tg_payments_total",
            description="Payment updates outcomes (pre_checkout/paid), by result",
        )


def new_instruments() -> TgInstruments:
    return TgInstruments(metrics.get_meter("app"))
