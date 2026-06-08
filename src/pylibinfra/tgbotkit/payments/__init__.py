"""payments-подсистема (generic-механика): timeout-safe pre_checkout, дедуп
charge/refund, invoice (XTR/фиат, составной чек, чаевые), subscription-линк,
refund-обёртка + tg_payments_* метрики. Регистрация dispatcher-level хендлеров —
через CommandBundle; бизнес (policy/usecase/gateway/диалоги) — в шаблоне.
"""

from .guard import ChargeGuard, RefundGuard
from .invoice import build_invoice
from .money import Money
from .precheckout import PreCheckoutDecision, answer_pre_checkout
from .refund import refund_charge
from .subscription import STARS_SUBSCRIPTION_PERIOD, build_subscription_link

__all__ = [
    "STARS_SUBSCRIPTION_PERIOD",
    "ChargeGuard",
    "Money",
    "PreCheckoutDecision",
    "RefundGuard",
    "answer_pre_checkout",
    "build_invoice",
    "build_subscription_link",
    "refund_charge",
]
