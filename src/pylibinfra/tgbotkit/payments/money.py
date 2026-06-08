"""Money — денежная величина в МИНОРНЫХ единицах валюты + код валюты. PURE-value.

amount_minor — наименьшая неделимая единица: центы для фиата (USD 1000 = $10.00);
для XTR (Telegram Stars) — целое число звёзд (Stars не делятся на сотые), т.е.
amount_minor == количество звёзд. currency — ISO-4217 для фиата либо "XTR".

Узкий value-тип без зависимости от aiogram, чтобы доменный слой шаблона (dto.py) мог
держать цену типизированно; LabeledPrice из него собирают invoice/subscription.
"""

from __future__ import annotations

from dataclasses import dataclass

XTR = "XTR"


@dataclass(frozen=True, slots=True)
class Money:
    amount_minor: int
    currency: str = XTR

    @property
    def is_stars(self) -> bool:
        return self.currency == XTR
