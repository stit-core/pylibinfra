"""build_invoice — kwargs для bot.send_invoice(chat_id, **invoice) с XTR-
нормализацией. Telegram Stars: currency="XTR", provider_token пустой; amount у
XTR — это количество звёзд (не сотые доли). Бизнес-цена/состав — на бэкенде.

Одна линия (`amount=`) — XTR-шорткат; составной чек — `prices=[LabeledPrice, …]`.
Чаевые (`max_tip_amount`/`suggested_tip_amounts`) — только фиат: Telegram отвергает
tips на XTR, поэтому на Stars они отбрасываются.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from aiogram.types import LabeledPrice

from .money import XTR


def build_invoice(
    *,
    title: str,
    description: str,
    payload: str,
    amount: int | None = None,
    prices: Sequence[LabeledPrice] | None = None,
    currency: str = XTR,
    label: str = "",
    provider_token: str | None = None,
    max_tip_amount: int | None = None,
    suggested_tip_amounts: Sequence[int] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    if prices is None:
        if amount is None:
            raise ValueError("build_invoice: pass exactly one of amount= or prices=")
        line_items = [LabeledPrice(label=label or title, amount=amount)]
    else:
        if amount is not None:
            raise ValueError("build_invoice: pass exactly one of amount= or prices=")
        line_items = list(prices)

    is_stars = currency == XTR
    invoice: dict[str, Any] = {
        "title": title,
        "description": description,
        "payload": payload,
        "currency": currency,
        "prices": line_items,
        "provider_token": None if is_stars else provider_token,
    }
    # Чаевые поддерживает только фиат — на XTR Telegram отвергнет max_tip_amount.
    if not is_stars and max_tip_amount is not None:
        invoice["max_tip_amount"] = max_tip_amount
        if suggested_tip_amounts is not None:
            invoice["suggested_tip_amounts"] = list(suggested_tip_amounts)
    invoice.update(extra)
    return invoice
