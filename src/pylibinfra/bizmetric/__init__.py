"""bizmetric — единая конвенция имён бизнес-метрик `biz_<domain>_<name>_<suffix>`.

Инструменты создаются на meter'е консьюмера, нарушение конвенции — `InvalidNameError`
при регистрации. Собственных сигналов кит не эмитит.
"""

from .bizmetric import (
    UNIT_EVENT,
    UNIT_ITEM,
    UNIT_SECONDS,
    InvalidNameError,
    counter,
    gauge,
    histogram,
    must_name,
    up_down_counter,
)

__all__ = [
    "UNIT_EVENT",
    "UNIT_ITEM",
    "UNIT_SECONDS",
    "InvalidNameError",
    "counter",
    "gauge",
    "histogram",
    "must_name",
    "up_down_counter",
]
