"""payments-подсистема (generic-механика): timeout-safe pre_checkout, дедуп
charge, XTR-invoice + tg_payments_* метрики. Регистрация dispatcher-level
хендлеров — через CommandBundle; бизнес (policy/usecase/gateway/диалоги) — в шаблоне.
"""

from .guard import guard_charge
from .invoice import build_invoice
from .precheckout import PreCheckoutDecision, answer_pre_checkout

__all__ = ["PreCheckoutDecision", "answer_pre_checkout", "build_invoice", "guard_charge"]
