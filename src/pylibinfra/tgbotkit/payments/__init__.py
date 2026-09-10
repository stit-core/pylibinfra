"""payments-подсистема: pre-checkout в дедлайн, дедуп платежей, инвойсы, подписки, возвраты.

Эмитит `tg_payments_total{stage,result}`; политика цены и выдачи — в сервисе.
"""

from .guard import CHARGE_PREFIX, REFUND_PREFIX, ChargeGuard, RefundGuard
from .invoice import build_invoice
from .money import XTR, Money
from .precheckout import PreCheckoutDecision, answer_pre_checkout
from .refund import refund_charge
from .subscription import STARS_SUBSCRIPTION_PERIOD, build_subscription_link

__all__ = [
    "CHARGE_PREFIX",
    "REFUND_PREFIX",
    "STARS_SUBSCRIPTION_PERIOD",
    "XTR",
    "ChargeGuard",
    "Money",
    "PreCheckoutDecision",
    "RefundGuard",
    "answer_pre_checkout",
    "build_invoice",
    "build_subscription_link",
    "refund_charge",
]
