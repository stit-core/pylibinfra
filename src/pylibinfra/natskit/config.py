from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BackpressureConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    max_in_flight: int = Field(default=100, validation_alias="NATS_MAX_IN_FLIGHT")


class JetStreamConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    enabled: bool = Field(default=False, validation_alias="NATS_JETSTREAM_ENABLED")
    stream_names: list[str] = Field(default_factory=list, validation_alias="NATS_JS_STREAM_NAMES")
    retention: str = Field(default="limits", validation_alias="NATS_JS_RETENTION")
    # секунды
    max_age: float = Field(default=168 * 3600, validation_alias="NATS_JS_MAX_AGE")
    max_in_flight: int = Field(default=100, validation_alias="NATS_JS_MAX_IN_FLIGHT")
    # секунды
    ack_wait: float = Field(default=30.0, validation_alias="NATS_JS_ACK_WAIT")
    max_deliver: int = Field(default=5, validation_alias="NATS_JS_MAX_DELIVER")
    # секунды задержки nak по попыткам: сервер берёт backoff[min(attempt-1, len-1)]
    backoff: list[float] = Field(
        default_factory=lambda: [1.0, 5.0, 15.0, 30.0], validation_alias="NATS_JS_BACKOFF"
    )
    flow_control: bool = Field(default=True, validation_alias="NATS_JS_FLOW_CONTROL")
    # секунды; nats требует idle_heartbeat при flow_control для push-консьюмеров
    idle_heartbeat: float = Field(default=5.0, validation_alias="NATS_JS_IDLE_HEARTBEAT")
    dlq_enabled: bool = Field(default=True, validation_alias="NATS_JS_DLQ_ENABLED")


class Config(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    url: str = Field(default="nats://localhost:4222", validation_alias="NATS_URL")
    # секунды
    connect_timeout: float = Field(default=5.0, validation_alias="NATS_CONNECT_TIMEOUT")
    # секунды
    reconnect_wait: float = Field(default=2.0, validation_alias="NATS_RECONNECT_WAIT")
    # -1 = бесконечно: consumer-бот не должен молча умирать после серии reconnect'ов
    max_reconnects: int = Field(default=-1, validation_alias="NATS_MAX_RECONNECTS")
    backpressure: BackpressureConfig = Field(default_factory=BackpressureConfig)
    jetstream: JetStreamConfig = Field(default_factory=JetStreamConfig)
