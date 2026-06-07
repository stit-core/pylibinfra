"""ObservabilityMiddleware (outer): span tg.update {type} + RED-метрики
(tg_updates_total{type,result}, tg_update_duration_seconds). Innermost из нашей
цепочки — видит исключение хендлера, помечает result=error и пробрасывает выше
(recovery его погасит). Покрывает и фоновые dialog-события: они идут через тот
же propagate_event("update").
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from pylibinfra.telemetry import span
from pylibinfra.tgbotkit.metrics import TgInstruments

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class ObservabilityMiddleware(BaseMiddleware):
    def __init__(self, instruments: TgInstruments) -> None:
        self._instr = instruments

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        update_type = str(getattr(event, "event_type", "unknown"))
        start = time.perf_counter()
        result = "ok"
        try:
            with span("tg.update", **{"tg.update_type": update_type}):
                return await handler(event, data)
        except Exception:
            result = "error"
            raise
        finally:
            attrs = {"type": update_type, "result": result}
            self._instr.updates_total.add(1, attrs)
            self._instr.update_duration.record(time.perf_counter() - start, attrs)
