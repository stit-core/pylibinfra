"""Config — public API grpckit. Сервер слушает UDS и/или TCP: co-located
sidecar'ы (endpoint-инференс) ходят по UDS (нулевой сетевой хоп), удалённые —
по TCP. Хотя бы один bind-target должен быть задан (валидируется в Server).
Имена env зеркалят стиль httpkit (GRPC_*; таймауты — секунды).
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 16 MiB — с запасом: типовой DetectTurn-кадр (8 c × 16k × 2 B = 256 KB) сильно
# ниже, но границу держим общей для любых gRPC-консьюмеров кита.
_DEFAULT_MAX_BYTES = 16 * 1024 * 1024


class Config(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    # TCP bind ("host:port"); пусто = TCP выключен (UDS-only sidecar).
    addr: str = Field(default="", validation_alias="GRPC_ADDR")
    # Unix-domain socket path; пусто = UDS выключен. Co-located транспорт.
    uds_path: str = Field(default="", validation_alias="GRPC_UDS_PATH")

    # graceful-drain: дать активным RPC доиграть перед закрытием (секунды).
    shutdown_grace: float = Field(default=15.0, validation_alias="GRPC_SHUTDOWN_GRACE")

    max_recv_bytes: int = Field(default=_DEFAULT_MAX_BYTES, validation_alias="GRPC_MAX_RECV_BYTES")
    max_send_bytes: int = Field(default=_DEFAULT_MAX_BYTES, validation_alias="GRPC_MAX_SEND_BYTES")

    # keepalive-enforcement. Клиент (golibinfra/grpcclient) шлёт keepalive-пинги
    # с PermitWithoutStream=true. Без этих опций gRPC-дефолты сервера штрафуют
    # пинги в idle-транспорте (permit_without_calls=0, порог 2 ч) и через 2 страйка
    # шлют GOAWAY too_many_pings, рвя соединение в паузах между RPC. Разрешаем
    # пинги без активных вызовов и держим min-интервал ниже client Time (30 с).
    keepalive_permit_without_calls: bool = Field(
        default=True, validation_alias="GRPC_KEEPALIVE_PERMIT_WITHOUT_CALLS"
    )
    # минимальный интервал между пингами клиента, секунды (зеркалит стиль таймаутов).
    keepalive_min_ping_interval: float = Field(
        default=10.0, validation_alias="GRPC_KEEPALIVE_MIN_PING_INTERVAL"
    )


def new_config() -> Config:
    return Config()
