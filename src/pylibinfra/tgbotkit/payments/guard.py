"""ChargeGuard — дедуп successful_payment по telegram_payment_charge_id.
Telegram доставляет апдеты at-least-once → один платёж может прийти дважды.
Reserve/commit/release в общем redis по образцу DeliveryDedup:

- reserve(charge_id): атомарный SET NX EX reserve_ttl — короткое окно «выдача в
  процессе». True = ключ захвачен (звонящий выполняет fulfill), False = дубликат
  (другой обработчик в процессе ИЛИ уже выдано → звонящий пропускает); на дубликате
  эмитит tg_payments_total{stage=paid,result=duplicate}.
- commit(charge_id): после успешного fulfill поднимает ключ до длинного окна ttl.
- release(charge_id): после транзиентного сбоя снимает резервацию, чтобы повторный
  апдейт того же платежа смог выполнить fulfill заново.
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


class ChargeGuard:
    def __init__(
        self, client: redis.Redis, ttl: float = 604800.0, reserve_ttl: float = 60.0
    ) -> None:
        self._redis = client
        self._ttl = max(int(ttl), 1)
        self._reserve_ttl = max(int(reserve_ttl), 1)

    async def reserve(self, charge_id: str) -> bool:
        fresh = await self._redis.set(f"tg:charge:{charge_id}", b"1", nx=True, ex=self._reserve_ttl)
        if fresh:
            return True
        _instr().payments_total.add(1, {"stage": "paid", "result": "duplicate"})
        return False

    async def commit(self, charge_id: str) -> None:
        await self._redis.set(f"tg:charge:{charge_id}", b"1", ex=self._ttl)

    async def release(self, charge_id: str) -> None:
        await self._redis.delete(f"tg:charge:{charge_id}")


class RefundGuard:
    """Дедуп события refunded_payment по telegram_payment_charge_id (отдельный ключ
    tg:refund:<id> — не пересекается с ChargeGuard). Reserve/commit/release как у
    ChargeGuard: откат выдачи (revoke) по возврату выполняется ровно один раз; на
    дубликате эмитит tg_payments_total{stage=refunded,result=duplicate}."""

    def __init__(
        self, client: redis.Redis, ttl: float = 604800.0, reserve_ttl: float = 60.0
    ) -> None:
        self._redis = client
        self._ttl = max(int(ttl), 1)
        self._reserve_ttl = max(int(reserve_ttl), 1)

    async def reserve(self, charge_id: str) -> bool:
        fresh = await self._redis.set(f"tg:refund:{charge_id}", b"1", nx=True, ex=self._reserve_ttl)
        if fresh:
            return True
        _instr().payments_total.add(1, {"stage": "refunded", "result": "duplicate"})
        return False

    async def commit(self, charge_id: str) -> None:
        await self._redis.set(f"tg:refund:{charge_id}", b"1", ex=self._ttl)

    async def release(self, charge_id: str) -> None:
        await self._redis.delete(f"tg:refund:{charge_id}")
