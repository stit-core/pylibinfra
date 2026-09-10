"""Money — цена в минорных единицах валюты, без зависимости от aiogram."""

from __future__ import annotations

from dataclasses import dataclass

XTR = "XTR"
"""Код валюты Telegram Stars: `amount_minor` в ней — целое число звёзд."""


@dataclass(frozen=True, slots=True)
class Money:
    """Сумма в наименьшей неделимой единице (центы у фиата, звёзды у `XTR`) и её валюта."""

    amount_minor: int
    currency: str = XTR

    @property
    def is_stars(self) -> bool:
        """Оплата звёздами: `provider_token` не нужен, чаевые недоступны."""
        return self.currency == XTR
