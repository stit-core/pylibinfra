"""ObservabilityMiddleware: спан `tg.update` и RED-метрики каждого апдейта."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from aiogram.types.update import UpdateTypeLookupError
from opentelemetry.trace import Status, StatusCode

from pylibinfra._internal import otelkit
from pylibinfra.tgbotkit.metrics import Instruments

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]

SPAN_NAME = "tg.update"


def update_type(event: TelegramObject) -> str:
    """Тип апдейта как label метрик и атрибут спана; `unknown`, если тип не определяется.

    `Update.event_type` — property, которая бросает `UpdateTypeLookupError` на апдейте
    незнакомого этой версии aiogram типа; наблюдаемость не имеет права падать из-за
    нового поля в Bot API.
    """
    try:
        return str(getattr(event, "event_type", "unknown"))
    except UpdateTypeLookupError:
        return "unknown"


class ObservabilityMiddleware(BaseMiddleware):
    """Считает `tg_updates_total`/`tg_update_duration_seconds` и открывает спан апдейта.

    Стоит внутри цепочки кита: видит исключение обработчика, помечает спан и
    `result=error`, после чего пробрасывает его в recovery. Фоновые события диалога
    учитываются наравне с апдейтами — они идут через тот же `propagate_event`.
    Отменённый апдейт (остановка процесса на середине) тоже `result=error`: он не
    обработан, и записывать его успехом значило бы врать про доставку.
    """

    def __init__(self, instruments: Instruments) -> None:
        self._instr = instruments
        self._tracer = otelkit.tracer("tgbotkit")

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        """Обрабатывает апдейт внутри спана, записывая исход в метрики в любом случае."""
        kind = update_type(event)
        start = time.perf_counter()
        result = "ok"
        try:
            with self._tracer.start_as_current_span(SPAN_NAME) as span:
                span.set_attribute("tg.update_type", kind)
                try:
                    return await handler(event, data)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    raise
        except BaseException:
            result = "error"
            raise
        finally:
            attrs = {"type": kind, "result": result}
            self._instr.updates.add(1, attrs)
            self._instr.duration.record(time.perf_counter() - start, attrs)
