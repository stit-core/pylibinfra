"""natskit — pub/sub поверх NATS: Bus (subscribe/subscribe_queue), типизированные
publish_json/subscribe_json/subscribe_queue_json, опц. JetStream (StreamBus:
батч-async публикация + дедуп по Nats-Msg-Id; poison-обработка consumer'а —
max_deliver→DLQ/term, NAK с backoff), panic-recovery в подписчиках и backpressure
через asyncio.Semaphore (NATS_MAX_IN_FLIGHT).

Subject и headers входящего сообщения доступны handler'у через
subject_from_context()/headers_from_context(). W3C trace-context инжектится в
nats-headers, чтобы consumer-span был child'ом producer-span'а. Эмитит
nats_messages_{published,consumed}_total (consumed status: ok|handler_error|panic|
dead), nats_{publish,handle}_duration_seconds, nats_inflight_messages.
"""

from .bus import (
    Bus,
    Handler,
    NatsBus,
    publish_json,
    subscribe_json,
    subscribe_queue_json,
)
from .config import BackpressureConfig, Config, JetStreamConfig
from .conn import Conn, new_conn
from .context import headers_from_context, subject_from_context
from .instrumentation import NatsInstruments
from .jetstream import (
    DisabledStreamBus,
    JetStreamDisabledError,
    JsBus,
    StreamBus,
    StreamMsg,
    new_stream_bus,
    publish_stream_json,
    subscribe_stream_json,
)
from .module import NatsProvider, new_bus, new_config, probe

__all__ = [
    "BackpressureConfig",
    "Bus",
    "Config",
    "Conn",
    "DisabledStreamBus",
    "Handler",
    "JetStreamConfig",
    "JetStreamDisabledError",
    "JsBus",
    "NatsBus",
    "NatsInstruments",
    "NatsProvider",
    "StreamBus",
    "StreamMsg",
    "headers_from_context",
    "new_bus",
    "new_config",
    "new_conn",
    "new_stream_bus",
    "probe",
    "publish_json",
    "publish_stream_json",
    "subject_from_context",
    "subscribe_json",
    "subscribe_queue_json",
    "subscribe_stream_json",
]
