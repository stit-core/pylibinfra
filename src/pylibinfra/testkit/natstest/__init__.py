"""natstest — in-memory `natskit.Bus`/`natskit.StreamBus` для тестов потребителя.

Публикации записываются, подписчики получают их синхронно — без NATS и Docker.
"""

from .recorder import Message, Recorder, new

__all__ = ["Message", "Recorder", "new"]
