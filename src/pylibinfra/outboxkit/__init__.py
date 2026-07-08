"""outboxkit — transactional-outbox: Bus вставляет событие в outbox-таблицу в той
же транзакции, что и бизнес-state; Worker батчами вычитывает unpublished и
публикует в JetStream через Transport (natskit.StreamBus), помечая published (одним
batch-update) только подтверждённые PubAck'ом. OUTBOX_PUBLISH_MODE выбирает batch
(async, best-effort порядок) или ordered (строгий порядок, стоп на первой ошибке).
Гарантия — at-least-once; id строки уходит как Nats-Msg-Id → переотправка на
ретрае дедуплицируется стримом. OUTBOX_MAX_ATTEMPTS>0 метит строку dead после N
неудач; retention партиций — OUTBOX_RETENTION_MONTHS (pgstore).

Core-порт Repository отделён от pgstore-реализации. Эмитит
outbox_events_{inserted,processed,dead}_total, outbox_batch_{size,duration_seconds},
outbox_publish_duration_seconds; outbox_lag_seconds/outbox_pending_count — opt-in
через BacklogReporter + register_backlog_gauges. Боевой Postgres-store — в
подпакете outboxkit.pgstore.
"""

from .bus import Bus, Publisher, publish_json
from .config import Config, PublishMode
from .instance import Outbox
from .metrics import (
    BacklogReporter,
    OutboxInstruments,
    SubjectBacklog,
    register_backlog_gauges,
)
from .module import OutboxProvider, new_config
from .outbox import Failure, Message, Repository, Store
from .transport import (
    OutMsg,
    StreamTransport,
    Transport,
    new_stream_transport,
    new_stream_transport_for,
)
from .worker import Worker

__all__ = [
    "BacklogReporter",
    "Bus",
    "Config",
    "Failure",
    "Message",
    "OutMsg",
    "Outbox",
    "OutboxInstruments",
    "OutboxProvider",
    "PublishMode",
    "Publisher",
    "Repository",
    "Store",
    "StreamTransport",
    "SubjectBacklog",
    "Transport",
    "Worker",
    "new_config",
    "new_stream_transport",
    "new_stream_transport_for",
    "publish_json",
    "register_backlog_gauges",
]
