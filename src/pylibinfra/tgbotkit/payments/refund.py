"""refund_charge — возврат Telegram Stars по `charge_id`."""

from __future__ import annotations

from aiogram import Bot

from pylibinfra.tgbotkit.metrics import Instruments, new_instruments


async def refund_charge(
    bot: Bot,
    user_id: int,
    charge_id: str,
    *,
    instruments: Instruments | None = None,
) -> bool:
    """Возвращает списанные звёзды; ошибку не глотает — решение о повторе за вызывающим.

    Эмитит `tg_payments_total{stage=refund,result}`, включая `result=error` перед пробросом.
    Дедуп входящего события `refunded_payment` — отдельно, в `RefundGuard`.
    """
    instr = instruments if instruments is not None else new_instruments()
    try:
        ok = await bot.refund_star_payment(user_id=user_id, telegram_payment_charge_id=charge_id)
    except Exception:
        instr.payments.add(1, {"stage": "refund", "result": "error"})
        raise
    instr.payments.add(1, {"stage": "refund", "result": "ok" if ok else "rejected"})
    return bool(ok)
