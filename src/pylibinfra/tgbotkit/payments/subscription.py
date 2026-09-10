"""build_subscription_link — рекуррентный инвойс-линк (подписка Telegram Stars)."""

from __future__ import annotations

from aiogram import Bot
from aiogram.types import LabeledPrice

from pylibinfra.tgbotkit.errors import PaymentError

from .money import Money

STARS_SUBSCRIPTION_PERIOD = 2_592_000
"""30 дней — единственный период, который Telegram принимает для XTR-подписки."""


async def build_subscription_link(
    bot: Bot,
    *,
    title: str,
    description: str,
    payload: str,
    price: Money,
    subscription_period: int = STARS_SUBSCRIPTION_PERIOD,
    label: str = "",
    provider_token: str | None = None,
) -> str:
    """Ссылка на подписку: Telegram списывает цену каждый период и шлёт `successful_payment`.

    Период XTR фиксирован Telegram'ом, поэтому иной валится `PaymentError` до вызова API,
    как и неположительная цена и фиат без `provider_token`. Отмена подписки —
    `refund_charge` по `charge_id` платежа.
    """
    if price.amount_minor <= 0:
        raise PaymentError("tgbotkit: build_subscription_link: price must be > 0")
    if price.is_stars and subscription_period != STARS_SUBSCRIPTION_PERIOD:
        raise PaymentError(
            "tgbotkit: build_subscription_link: XTR requires "
            f"subscription_period={STARS_SUBSCRIPTION_PERIOD} (30d)"
        )
    if not price.is_stars and not provider_token:
        raise PaymentError(
            "tgbotkit: build_subscription_link: provider_token is required for currency != XTR"
        )
    return await bot.create_invoice_link(
        title=title,
        description=description,
        payload=payload,
        currency=price.currency,
        prices=[LabeledPrice(label=label or title, amount=price.amount_minor)],
        subscription_period=subscription_period,
        provider_token=None if price.is_stars else provider_token,
    )
