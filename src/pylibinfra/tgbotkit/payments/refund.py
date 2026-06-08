"""refund_charge — возврат Telegram Stars по charge_id (обёртка над
bot.refund_star_payment). Эмитит tg_payments_total{stage=refund,result}. Ошибку НЕ
глотает — пробрасывает наверх (вызывающий решает, ретраить ли), пометив result=error.

Дедуп входящего события refunded_payment (Telegram доставляет at-least-once, как
successful_payment) — в RefundGuard (guard.py), отдельным ключом от ChargeGuard.
"""

from __future__ import annotations

from aiogram import Bot

from pylibinfra.tgbotkit.metrics import TgInstruments, new_instruments

_instruments: TgInstruments | None = None


def _instr() -> TgInstruments:
    global _instruments
    if _instruments is None:
        _instruments = new_instruments()
    return _instruments


async def refund_charge(bot: Bot, user_id: int, charge_id: str) -> bool:
    try:
        ok = await bot.refund_star_payment(user_id=user_id, telegram_payment_charge_id=charge_id)
    except Exception:
        _instr().payments_total.add(1, {"stage": "refund", "result": "error"})
        raise
    _instr().payments_total.add(1, {"stage": "refund", "result": "ok" if ok else "rejected"})
    return bool(ok)
