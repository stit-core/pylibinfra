"""notifications-подсистема: durable-подписки JetStream и доставка их событий в Telegram.

Subject'ы, дедуп-ключи и маппинг «пользователь → чат» — в сервисе; кит ничего не эмитит.
"""

from .dedup import DeliveryDedup
from .delivery import Delivery, is_markup_error
from .router import DeliverHandler, NotificationRouter, Route
from .runner import NotificationRunner

__all__ = [
    "DeliverHandler",
    "Delivery",
    "DeliveryDedup",
    "NotificationRouter",
    "NotificationRunner",
    "Route",
    "is_markup_error",
]
