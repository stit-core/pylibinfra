"""answer_pre_checkout — ответ на `pre_checkout_query` в дедлайн Telegram, fail-closed."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from aiogram import Bot
from aiogram.types import PreCheckoutQuery

from pylibinfra.logkit import Logger, nop
from pylibinfra.tgbotkit.metrics import Instruments, new_instruments

PreCheckoutDecision = Callable[[PreCheckoutQuery], Awaitable[bool]]
"""Проверка платежа на бэкенде; `True` — подтвердить списание."""

DEFAULT_DEADLINE = 9.0
DEFAULT_ERROR_MESSAGE = "Payment validation failed, please try again."


async def answer_pre_checkout(
    bot: Bot,
    query: PreCheckoutQuery,
    decide: PreCheckoutDecision,
    *,
    deadline: float = DEFAULT_DEADLINE,
    error_message: str = DEFAULT_ERROR_MESSAGE,
    instruments: Instruments | None = None,
    log: Logger | None = None,
) -> bool:
    """Отвечает Telegram до его 10-секундного дедлайна; таймаут и сбой `decide` — отказ.

    Цену и состав чека `decide` обязан проверять на бэкенде: `total_amount` и
    `invoice_payload` приходят от клиента и источником истины не являются.
    Эмитит `tg_payments_total{stage=pre_checkout,result}`, включая `result=error`,
    когда упал сам ответ Telegram (исключение пробрасывается вызывающему).
    Причину отказа видно только здесь: без `log` она гаснет вместе с исключением,
    и в проде остаётся один счётчик без объяснения.
    """
    instr = instruments if instruments is not None else new_instruments()
    logger = log if log is not None else nop()
    try:
        ok = await asyncio.wait_for(decide(query), timeout=deadline)
        result = "ok" if ok else "rejected"
    except Exception as exc:
        ok, result = False, "error"
        logger.error("tgbotkit: pre-checkout validation failed, refusing", err=repr(exc))
    try:
        await bot.answer_pre_checkout_query(
            query.id, ok=ok, error_message=None if ok else error_message
        )
    except Exception:
        instr.payments.add(1, {"stage": "pre_checkout", "result": "error"})
        raise
    instr.payments.add(1, {"stage": "pre_checkout", "result": result})
    return ok
