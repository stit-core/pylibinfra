"""audittest — in-memory `auditkit.Logger` для unit-тестов домена без Postgres."""

from .recorder import Recorder

__all__ = ["Recorder"]
