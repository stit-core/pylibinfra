"""notifications-подсистема: durable JetStream-подписки (natskit) → безопасная
доставка в Telegram через Delivery (обёртка над BgManager). Точка расширения —
Notifier/NotifierBundle (или сахар NotificationRouter.on). Какие subject'ы,
deliver_*, маппинг user→chat, дедуп-ключи — в шаблоне.
"""

from .dedup import DeliveryDedup
from .delivery import Delivery
from .router import DeliverHandler, NotificationRouter
from .runner import NotificationRunner

__all__ = [
    "DeliverHandler",
    "Delivery",
    "DeliveryDedup",
    "NotificationRouter",
    "NotificationRunner",
]
