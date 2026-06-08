"""build_subscription_link — рекуррентный инвойс-линк (Telegram Stars subscription).

create_invoice_link(subscription_period=…) выдаёт ссылку на подписку: Telegram сам
списывает price каждый период и шлёт successful_payment с is_recurring. Для XTR период
фиксирован Telegram'ом — 30 дней (2_592_000 c); иные значения он отвергает, поэтому
валидируем заранее. Отмена/возврат подписки — refund_charge по charge_id платежа.
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.types import LabeledPrice

from .money import Money

STARS_SUBSCRIPTION_PERIOD = 2_592_000  # 30 дней — единственный период для XTR-подписок


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
    if price.is_stars and subscription_period != STARS_SUBSCRIPTION_PERIOD:
        raise ValueError(
            f"XTR subscriptions require subscription_period={STARS_SUBSCRIPTION_PERIOD} (30d)"
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
