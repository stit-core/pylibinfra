"""audittest — in-memory фейк auditkit.Recorder/Store с ассерт-хелперами для
тестов консьюмера (без БД)."""

from .recorder import Recorder

__all__ = ["Recorder"]
