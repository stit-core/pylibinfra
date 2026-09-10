"""Config — публичный env-контракт gRPC-сервера и его проверка."""

from __future__ import annotations

from pydantic import Field, model_validator

from pylibinfra._internal import configkit
from pylibinfra._internal.configkit import KitSettings

DEFAULT_ADDR = ":9090"
DEFAULT_MAX_MSG_SIZE = 4 * 1024 * 1024
DEFAULT_KEEPALIVE_TIME = 7200.0
DEFAULT_KEEPALIVE_TIMEOUT = 20.0
# Не строже клиентского keepalive golibinfra (30s): связка китов «из коробки» не
# должна получать GOAWAY too_many_pings.
DEFAULT_KEEPALIVE_MIN_TIME = 20.0


class ConfigError(configkit.ConfigError):
    """Невалидный конфиг сервера: отрицательные лимиты, половина TLS-пары, нечего биндить."""


class Config(KitSettings):
    """Публичный env-контракт grpckit; нулевой конфиг даёт рабочий сервер на `:9090`."""

    addr: str = Field(default=DEFAULT_ADDR, validation_alias="GRPC_ADDR")
    uds_path: str = Field(default="", validation_alias="GRPC_UDS_PATH")

    max_recv_msg_size: int = Field(
        default=DEFAULT_MAX_MSG_SIZE, validation_alias="GRPC_MAX_RECV_MSG_SIZE"
    )
    max_send_msg_size: int = Field(
        default=DEFAULT_MAX_MSG_SIZE, validation_alias="GRPC_MAX_SEND_MSG_SIZE"
    )
    max_concurrent_streams: int = Field(default=0, validation_alias="GRPC_MAX_CONCURRENT_STREAMS")

    reflection: bool = Field(default=False, validation_alias="GRPC_REFLECTION")
    shutdown_grace: float = Field(default=10.0, validation_alias="GRPC_SHUTDOWN_GRACE")

    health_interval: float = Field(default=10.0, validation_alias="GRPC_HEALTH_INTERVAL")
    health_probe_timeout: float = Field(default=5.0, validation_alias="GRPC_HEALTH_PROBE_TIMEOUT")

    tls_cert_file: str = Field(default="", validation_alias="GRPC_TLS_CERT_FILE")
    tls_key_file: str = Field(default="", validation_alias="GRPC_TLS_KEY_FILE")

    keepalive_time: float = Field(
        default=DEFAULT_KEEPALIVE_TIME, validation_alias="GRPC_KEEPALIVE_TIME"
    )
    keepalive_timeout: float = Field(
        default=DEFAULT_KEEPALIVE_TIMEOUT, validation_alias="GRPC_KEEPALIVE_TIMEOUT"
    )
    keepalive_min_time: float = Field(
        default=DEFAULT_KEEPALIVE_MIN_TIME, validation_alias="GRPC_KEEPALIVE_MIN_TIME"
    )
    keepalive_permit_without_stream: bool = Field(
        default=True, validation_alias="GRPC_KEEPALIVE_PERMIT_WITHOUT_STREAM"
    )

    @model_validator(mode="after")
    def _check(self) -> Config:
        for env, value in (
            ("GRPC_MAX_RECV_MSG_SIZE", self.max_recv_msg_size),
            ("GRPC_MAX_SEND_MSG_SIZE", self.max_send_msg_size),
            ("GRPC_MAX_CONCURRENT_STREAMS", self.max_concurrent_streams),
            ("GRPC_SHUTDOWN_GRACE", self.shutdown_grace),
            ("GRPC_HEALTH_INTERVAL", self.health_interval),
            ("GRPC_HEALTH_PROBE_TIMEOUT", self.health_probe_timeout),
            ("GRPC_KEEPALIVE_TIME", self.keepalive_time),
            ("GRPC_KEEPALIVE_TIMEOUT", self.keepalive_timeout),
            ("GRPC_KEEPALIVE_MIN_TIME", self.keepalive_min_time),
        ):
            if value < 0:
                raise ValueError(f"{env} must be >= 0")
        if bool(self.tls_cert_file) != bool(self.tls_key_file):
            raise ValueError("GRPC_TLS_CERT_FILE and GRPC_TLS_KEY_FILE must be set both or neither")
        # Пробы гоняются параллельно, поэтому цикл переоценки укладывается в период
        # ровно тогда, когда дедлайн одной пробы не длиннее периода.
        if self.health_interval > 0 and self.health_probe_timeout > self.health_interval:
            raise ValueError("GRPC_HEALTH_PROBE_TIMEOUT must not exceed GRPC_HEALTH_INTERVAL")
        if not self.addr and not self.uds_path:
            raise ValueError("GRPC_ADDR and GRPC_UDS_PATH are both empty: nothing to bind")
        return self


def new_config() -> Config:
    """Читает Config из ENV и валидирует его: ошибка конфигурации падает на старте."""
    return configkit.build(Config, "grpckit", ConfigError)
