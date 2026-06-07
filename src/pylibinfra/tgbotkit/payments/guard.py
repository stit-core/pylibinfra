"""guard_charge — дедуп successful_payment по telegram_payment_charge_id.
Telegram доставляет апдеты at-least-once → один платёж может прийти дважды;
атомарный SET NX EX в общем redis. True = уже обрабатывали (шаблон должен
пропустить повторный fulfill). Эмитит tg_payments_total{result=duplicate}.
"""

from __future__ import annotations

import redis.asyncio as redis

from pylibinfra.tgbotkit.metrics import TgInstruments, new_instruments

_instruments: TgInstruments | None = None


def _instr() -> TgInstruments:
    global _instruments
    if _instruments is None:
        _instruments = new_instruments()
    return _instruments


async def guard_charge(client: redis.Redis, charge_id: str, *, ttl: float = 604800.0) -> bool:
    fresh = await client.set(f"tg:charge:{charge_id}", b"1", nx=True, ex=max(int(ttl), 1))
    if fresh:
        return False
    _instr().payments_total.add(1, {"stage": "paid", "result": "duplicate"})
    return True
