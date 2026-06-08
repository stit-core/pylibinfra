from purgatory import AsyncCircuitBreakerFactory
from purgatory.domain.messages.events import ContextChanged
from purgatory.domain.model import OpenedState
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .metrics import Instruments

# Исключение, которым открытый брейкер фейлит вызов до обращения к transport.
BreakerOpenError = OpenedState

BREAKER_NAME = "httpclient"

# purgatory-имена состояний → gobreaker-совместимые для labels метрик.
_STATE_LABEL = {"closed": "closed", "opened": "open", "half-opened": "half-open"}


class BreakerConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    failure_threshold: int = Field(
        default=5, validation_alias="HTTPCLIENT_BREAKER_FAILURE_THRESHOLD"
    )
    # секунды (TTL открытого состояния до пробного half-open запроса)
    recovery_timeout: float = Field(
        default=30.0, validation_alias="HTTPCLIENT_BREAKER_RECOVERY_TIMEOUT"
    )
    success_threshold: int = Field(
        default=3, validation_alias="HTTPCLIENT_BREAKER_SUCCESS_THRESHOLD"
    )
    # Имя брейкера = label {"name"} в http_client_breaker_* метриках. Уникальное на
    # каждый именованный backend-клиент → per-API breaker-метрики без коллизий.
    name: str = Field(default=BREAKER_NAME, validation_alias="HTTPCLIENT_BREAKER_NAME")


def new_breaker(cfg: BreakerConfig, instr: Instruments) -> AsyncCircuitBreakerFactory:
    factory = AsyncCircuitBreakerFactory(
        default_threshold=cfg.failure_threshold,
        default_ttl=cfg.recovery_timeout,
    )

    # purgatory отдаёт только новое состояние; предыдущее держим сами, чтобы
    # эмитить transitions с from/to и крутить gauge open (+1 при входе в open,
    # -1 при выходе) как в gobreaker OnStateChange.
    prev: dict[str, str] = {}

    def on_event(name: str, evt_type: str, event: object) -> None:
        if evt_type != "state_changed" or not isinstance(event, ContextChanged):
            return
        from_state = prev.get(name, "closed")
        to_state = _STATE_LABEL.get(event.state, event.state)
        prev[name] = to_state

        instr.breaker_transitions.add(1, {"name": name, "from": from_state, "to": to_state})
        if to_state == "open":
            instr.breaker_open.add(1, {"name": name})
        elif from_state == "open":
            instr.breaker_open.add(-1, {"name": name})

    factory.add_listener(on_event)
    return factory
