"""logtest — capture-логгер `logkit.Logger`: тест проверяет уровень, сообщение и поля
записи, не разбирая stdout."""

from .recorder import Capture, Entry, capture_at

__all__ = ["Capture", "Entry", "capture_at"]
