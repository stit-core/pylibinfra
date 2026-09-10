"""natskit — pub/sub поверх NATS: `Bus` (Core, fire-and-forget) и `StreamBus` (JetStream,
at-least-once с дедупом, DLQ и NAK-backoff), backpressure, graceful `close`, health-проба.

Эмитит `nats_messages_{published,consumed}_total`, `nats_{publish,handle}_duration_seconds`,
`nats_inflight_messages` и спаны `natskit.publish`/`natskit.handle` (trace-propagation
через заголовки сообщения). Конфиг и полный список сигналов — `README.md` кита.
"""

from .bus import (
    Bus,
    NatsBus,
    new_bus,
    publish_json,
    subscribe_json,
    subscribe_queue_json,
)
from .client import Client, new_client, probe
from .config import (
    DEFAULT_INSTANCE,
    BackpressureConfig,
    Config,
    JetStreamConfig,
    config_without_env,
    env_prefix,
    new_config,
    new_config_prefixed,
    redact_url,
)
from .conn import Conn, new_conn
from .context import headers_from_context, message_context, subject_from_context
from .errors import (
    ClosedError,
    ConfigError,
    ConnectionClosedError,
    JetStreamDisabledError,
    NatskitError,
    PublishError,
    StreamError,
    SubscribeError,
    TerminalError,
    is_terminal,
)
from .instrumentation import NatsInstruments
from .jetstream import (
    HEADER_DLQ_CONSUMER,
    HEADER_DLQ_DELIVERIES,
    HEADER_DLQ_ERROR,
    HEADER_DLQ_INSTANCE,
    HEADER_DLQ_MSG_ID,
    HEADER_DLQ_ORIGIN,
    HEADER_DLQ_REASON,
    HEADER_DLQ_STREAM,
    REASON_MAX_DELIVER,
    REASON_TERMINAL,
    DisabledStreamBus,
    JsBus,
    StreamBus,
    new_stream_bus,
    publish_stream_json,
    subscribe_stream_json,
)
from .module import NatsProvider
from .publish import MSG_ID_HEADER, PublishSpec, StreamMsg, new_publish_spec
from .subjects import subject_matches
from .subscription import Handler, Subscription

__all__ = [
    "DEFAULT_INSTANCE",
    "HEADER_DLQ_CONSUMER",
    "HEADER_DLQ_DELIVERIES",
    "HEADER_DLQ_ERROR",
    "HEADER_DLQ_INSTANCE",
    "HEADER_DLQ_MSG_ID",
    "HEADER_DLQ_ORIGIN",
    "HEADER_DLQ_REASON",
    "HEADER_DLQ_STREAM",
    "MSG_ID_HEADER",
    "REASON_MAX_DELIVER",
    "REASON_TERMINAL",
    "BackpressureConfig",
    "Bus",
    "Client",
    "ClosedError",
    "Config",
    "ConfigError",
    "Conn",
    "ConnectionClosedError",
    "DisabledStreamBus",
    "Handler",
    "JetStreamConfig",
    "JetStreamDisabledError",
    "JsBus",
    "NatsBus",
    "NatsInstruments",
    "NatsProvider",
    "NatskitError",
    "PublishError",
    "PublishSpec",
    "StreamBus",
    "StreamError",
    "StreamMsg",
    "SubscribeError",
    "Subscription",
    "TerminalError",
    "config_without_env",
    "env_prefix",
    "headers_from_context",
    "is_terminal",
    "message_context",
    "new_bus",
    "new_client",
    "new_config",
    "new_config_prefixed",
    "new_conn",
    "new_publish_spec",
    "new_stream_bus",
    "probe",
    "publish_json",
    "publish_stream_json",
    "redact_url",
    "subject_from_context",
    "subject_matches",
    "subscribe_json",
    "subscribe_queue_json",
    "subscribe_stream_json",
]
