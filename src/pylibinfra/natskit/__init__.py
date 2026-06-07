"""natskit — pub/sub поверх NATS: Bus, типизированные publish_json/subscribe_json,
опц. JetStream, panic-recovery в подписчиках и backpressure через
asyncio.Semaphore (NATS_MAX_IN_FLIGHT).

W3C trace-context инжектится в nats-headers, чтобы consumer-span был child'ом
producer-span'а. Эмитит nats_messages_{published,consumed}_total,
nats_{publish,handle}_duration_seconds, nats_inflight_messages.
"""

from .bus import Bus, Handler, NatsBus, publish_json, subscribe_json
from .config import BackpressureConfig, Config, JetStreamConfig
from .conn import Conn, new_conn
from .instrumentation import NatsInstruments
from .jetstream import (
    DisabledStreamBus,
    JetStreamDisabledError,
    JsBus,
    StreamBus,
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
    "new_bus",
    "new_config",
    "new_conn",
    "new_stream_bus",
    "probe",
    "publish_json",
    "publish_stream_json",
    "subscribe_json",
    "subscribe_stream_json",
]
