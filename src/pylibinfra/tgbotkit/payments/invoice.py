"""build_invoice — kwargs для `bot.send_invoice` с нормализацией под Telegram Stars."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from aiogram.types import LabeledPrice

from pylibinfra.tgbotkit.errors import PaymentError

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
    """Собирает инвойс из одной суммы (`amount=`) либо составного чека (`prices=`).

    На `XTR` `provider_token` пустой, а чаевые отбрасываются — Telegram их для звёзд
    отвергает; фиат без `provider_token` отвергается здесь же. Ровно один из
    `amount`/`prices` обязателен, сумма — положительная, список строк чека —
    непустой; иначе `PaymentError` вместо `400` от Telegram.
    """
    if (amount is None) == (prices is None):
        raise PaymentError("tgbotkit: build_invoice: pass exactly one of amount= or prices=")
    if amount is not None and amount <= 0:
        raise PaymentError("tgbotkit: build_invoice: amount must be > 0")
    line_items = (
        list(prices)
        if prices is not None
        else [LabeledPrice(label=label or title, amount=amount or 0)]
    )
    if not line_items:
        raise PaymentError("tgbotkit: build_invoice: prices must not be empty")

    is_stars = currency == XTR
    if not is_stars and not provider_token:
        raise PaymentError(
            f"tgbotkit: build_invoice: provider_token is required for currency != {XTR}"
        )
    invoice: dict[str, Any] = {
        "title": title,
        "description": description,
        "payload": payload,
        "currency": currency,
        "prices": line_items,
        "provider_token": None if is_stars else provider_token,
    }
    if not is_stars and max_tip_amount is not None:
        invoice["max_tip_amount"] = max_tip_amount
        if suggested_tip_amounts is not None:
            invoice["suggested_tip_amounts"] = list(suggested_tip_amounts)
    invoice.update(extra)
    return invoice
