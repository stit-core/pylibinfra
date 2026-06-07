"""answer_pre_checkout — ответ на pre_checkout_query с timeout-гардом к дедлайну
Telegram (10с): если decide() не успел/упал — fail-closed (ok=False), иначе
платёж зависнет. ВАЖНО: цену/состав проверять на бэкенде ВНУТРИ decide, НЕ
доверять query.total_amount/payload как источнику истины (16). Эмитит
tg_payments_total{result}.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from aiogram import Bot
from aiogram.types import PreCheckoutQuery

from pylibinfra.tgbotkit.metrics import TgInstruments, new_instruments

PreCheckoutDecision = Callable[[PreCheckoutQuery], Awaitable[bool]]

_instruments: TgInstruments | None = None


def _instr() -> TgInstruments:
    global _instruments
    if _instruments is None:
        _instruments = new_instruments()
    return _instruments


async def answer_pre_checkout(
    bot: Bot,
    query: PreCheckoutQuery,
    decide: PreCheckoutDecision,
    *,
    deadline: float = 9.0,
    error_message: str = "Payment validation failed, please try again.",
) -> bool:
    result = "ok"
    ok = False
    try:
        ok = await asyncio.wait_for(decide(query), timeout=deadline)
        result = "ok" if ok else "rejected"
    except Exception:
        result = "error"  # timeout/исключение → fail-closed
        ok = False
    await bot.answer_pre_checkout_query(
        query.id, ok=ok, error_message=None if ok else error_message
    )
    _instr().payments_total.add(1, {"stage": "pre_checkout", "result": result})
    return ok
