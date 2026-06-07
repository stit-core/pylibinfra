"""outboxkit — transactional-outbox: Bus вставляет событие в outbox-таблицу в
той же транзакции, что и бизнес-state; Worker батчами вычитывает unpublished и
публикует в natskit.Bus.

Core-порт Store отделён от pgstore-реализации, чтобы доменный код не зависел от
Postgres. Эмитит outbox_events_{inserted,processed}_total, outbox_batch_size,
outbox_batch_duration_seconds, outbox_publish_duration_seconds; outbox_lag_seconds
эмитится, если Store реализует LagReporter (opt-in через register_lag_gauge).
Боевой Postgres-store — в подпакете outboxkit.pgstore.
"""

from .bus import Bus, Publisher, publish_json
from .config import Config
from .metrics import OutboxInstruments, register_lag_gauge
from .module import OutboxProvider, new_config
from .outbox import LagReporter, Message, Store
from .worker import Worker

__all__ = [
    "Bus",
    "Config",
    "LagReporter",
    "Message",
    "OutboxInstruments",
    "OutboxProvider",
    "Publisher",
    "Store",
    "Worker",
    "new_config",
    "publish_json",
    "register_lag_gauge",
]
