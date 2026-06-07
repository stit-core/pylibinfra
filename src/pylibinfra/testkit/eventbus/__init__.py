"""eventbus — in-memory recorder-Bus: записывает опубликованные события и отдаёт
их для ассертов (удовлетворяет outboxkit.Publisher и natskit.Bus.publish)."""

from .recorder import Event, Recorder

__all__ = ["Event", "Recorder"]
