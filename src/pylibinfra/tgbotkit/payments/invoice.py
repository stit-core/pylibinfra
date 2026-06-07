"""build_invoice — kwargs для bot.send_invoice(chat_id, **invoice) с XTR-
нормализацией. Telegram Stars: currency="XTR", provider_token пустой; amount у
XTR — это количество звёзд (не сотые доли). Бизнес-цена/состав — на бэкенде.
"""

from __future__ import annotations

from typing import Any

from aiogram.types import LabeledPrice


def build_invoice(
    *,
    title: str,
    description: str,
    payload: str,
    amount: int,
    currency: str = "XTR",
    label: str = "",
    provider_token: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    token = None if currency == "XTR" else provider_token
    return {
        "title": title,
        "description": description,
        "payload": payload,
        "currency": currency,
        "prices": [LabeledPrice(label=label or title, amount=amount)],
        "provider_token": token,
        **extra,
    }
