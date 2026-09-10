"""ChargeGuard и RefundGuard — выдача и откуп выполняются ровно один раз на платёж."""

from __future__ import annotations

import redis.asyncio as redis

from pylibinfra.idkit import new_v7
from pylibinfra.tgbotkit.metrics import Instruments, new_instruments
from pylibinfra.tgbotkit.reservation import Reservation

CHARGE_PREFIX = "tg:charge:"
REFUND_PREFIX = "tg:refund:"


class _Guard:
    """Резерв `telegram_payment_charge_id` в общем Redis по образцу `DeliveryDedup`."""

    _prefix = ""
    _stage = ""

    def __init__(
        self,
        client: redis.Redis,
        ttl: float = 604800.0,
        reserve_ttl: float = 60.0,
        instruments: Instruments | None = None,
    ) -> None:
        self._slots = Reservation(client, self._prefix, ttl=ttl, reserve_ttl=reserve_ttl)
        self._instr = instruments if instruments is not None else new_instruments()

    async def reserve(self, charge_id: str) -> str | None:
        """Токен владения или `None` — платёж уже обработан или обрабатывается сейчас.

        `None` считается в `tg_payments_total{result=duplicate}`. Токен обязателен для
        `commit`/`release`: он не даёт снять чужой резерв после истечения своего.
        """
        token = new_v7()
        if await self._slots.reserve(charge_id, token):
            return token
        self._instr.payments.add(1, {"stage": self._stage, "result": "duplicate"})
        return None

    async def commit(self, charge_id: str, token: str) -> bool:
        """Фиксирует обработку платежа на длинное окно; `False` — резерв уже не наш.

        `False` означает, что `reserve_ttl` истёк и платёж мог быть обработан повторно
        другим воркером: вызывающему стоит записать это в лог, а не молчать.
        """
        return await self._slots.commit(charge_id, token)

    async def release(self, charge_id: str, token: str) -> bool:
        """Снимает свой резерв после транзиентного сбоя; `False` — резерв уже не наш."""
        return await self._slots.release(charge_id, token)


class ChargeGuard(_Guard):
    """Дедуп `successful_payment`: Telegram доставляет платёжный апдейт at-least-once."""

    _prefix = CHARGE_PREFIX
    _stage = "paid"


class RefundGuard(_Guard):
    """Дедуп `refunded_payment` отдельным ключом — возврат и выдача одного платежа независимы."""

    _prefix = REFUND_PREFIX
    _stage = "refunded"
