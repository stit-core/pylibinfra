"""outboxkit — transactional outbox: `Bus` пишет событие в таблицу в транзакции домена,
`Worker` тиком-транзакцией (list → publish → mark) публикует его в JetStream.

Гарантия — at-least-once: id строки уходит как `Nats-Msg-Id`, и переотправка на ретрае
дедуплицируется стримом. Эмитит `outbox_events_{inserted,processed,dead}_total`,
`outbox_revived_total`, `outbox_batch_{size,duration_seconds}`,
`outbox_publish_duration_seconds`, а через `BacklogReporter` — `outbox_lag_seconds`,
`outbox_pending_count` и `outbox_backlog_errors_total`. Боевой store — `outboxkit.pgstore`.
"""

from .bus import Bus, Publisher, encode_payload
from .config import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_INSTANCE,
    DEFAULT_TICK_TIMEOUT,
    Config,
    PublishMode,
    new_config,
)
from .errors import BusyError, ConfigError, NotSupportedError, NoTxError, OutboxError
from .instance import Outbox, new_outbox
from .metrics import (
    BacklogRegistration,
    BacklogReporter,
    OutboxInstruments,
    SubjectBacklog,
    register_backlog_gauges,
)
from .module import OutboxInstanceProvider, OutboxProvider
from .outbox import (
    DeadReviver,
    Failure,
    Maintainer,
    Message,
    MessageRef,
    Repository,
    RepositoryFactory,
    ReviveFilter,
)
from .transport import OutMsg, StreamTransport, Transport, new_stream_transport
from .worker import COMMIT_GRACE, MAX_LAST_ERROR, TickResult, Worker

__all__ = [
    "COMMIT_GRACE",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_INSTANCE",
    "DEFAULT_TICK_TIMEOUT",
    "MAX_LAST_ERROR",
    "BacklogRegistration",
    "BacklogReporter",
    "Bus",
    "BusyError",
    "Config",
    "ConfigError",
    "DeadReviver",
    "Failure",
    "Maintainer",
    "Message",
    "MessageRef",
    "NoTxError",
    "NotSupportedError",
    "OutMsg",
    "Outbox",
    "OutboxError",
    "OutboxInstanceProvider",
    "OutboxInstruments",
    "OutboxProvider",
    "PublishMode",
    "Publisher",
    "Repository",
    "RepositoryFactory",
    "ReviveFilter",
    "StreamTransport",
    "SubjectBacklog",
    "TickResult",
    "Transport",
    "Worker",
    "encode_payload",
    "new_config",
    "new_outbox",
    "new_stream_transport",
    "register_backlog_gauges",
]
