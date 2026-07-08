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
    # секунды (Go: 168h)
    max_age: float = Field(default=168 * 3600, validation_alias="NATS_JS_MAX_AGE")
    # секунды (Go: 2m) — окно дедупа стрима по Nats-Msg-Id: в его пределах повторная
    # публикация с тем же id молча дедуплицируется (effectively-once при ретрае продьюсера).
    duplicates: float = Field(default=120.0, validation_alias="NATS_JS_DUPLICATES")
    max_in_flight: int = Field(default=100, validation_alias="NATS_JS_MAX_IN_FLIGHT")
    # секунды
    ack_wait: float = Field(default=30.0, validation_alias="NATS_JS_ACK_WAIT")
    # Consumer redelivery / poison handling (равняем на Go):
    #   max_deliver <= 0 — без ограничения (бесконечный ределивери; backstop выключен);
    #   nak_backoff  0   — мгновенный NAK, иначе NAK с задержкой (backoff);
    #   dlq_subject  ""  — на исчерпании попыток только term; иначе перед term'ом
    #                      сообщение republish'ится в dlq_subject (best-effort).
    max_deliver: int = Field(default=0, validation_alias="NATS_JS_MAX_DELIVER")
    # секунды
    nak_backoff: float = Field(default=0.0, validation_alias="NATS_JS_NAK_BACKOFF")
    dlq_subject: str = Field(default="", validation_alias="NATS_JS_DLQ_SUBJECT")


class Config(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    url: str = Field(default="nats://localhost:4222", validation_alias="NATS_URL")
    # секунды
    connect_timeout: float = Field(default=5.0, validation_alias="NATS_CONNECT_TIMEOUT")
    # секунды
    reconnect_wait: float = Field(default=2.0, validation_alias="NATS_RECONNECT_WAIT")
    # Go-дефолт 60; nats-py трактует -1 как бесконечный reconnect (см. conn.py).
    max_reconnects: int = Field(default=60, validation_alias="NATS_MAX_RECONNECTS")
    backpressure: BackpressureConfig = Field(default_factory=BackpressureConfig)
    jetstream: JetStreamConfig = Field(default_factory=JetStreamConfig)
