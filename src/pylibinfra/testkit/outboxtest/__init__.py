"""outboxtest — фейк `outboxkit.Publisher` для тестов домена без Postgres и NATS."""

from .recorder import Event, Recorder, new

__all__ = ["Event", "Recorder", "new"]
