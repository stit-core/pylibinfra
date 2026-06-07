"""clockkit — Clock-port + production-реализация и тестовые фейки."""

from .clock import Clock, Real
from .fakes import Fixed, Tickable
from .module import ClockProvider

__all__ = ["Clock", "ClockProvider", "Fixed", "Real", "Tickable"]
