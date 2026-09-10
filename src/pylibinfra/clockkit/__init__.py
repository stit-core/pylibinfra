"""clockkit — Clock-порт (now/since/sleep), production-реализация Real и фейки.

Фейки лежат в этом же пакете: `Fixed` — застывший момент, `Tickable` — время
двигает тест (`advance`/`set` будят `sleep`). Кит ничего не эмитит.
"""

from .clock import Clock, Real
from .fakes import Fixed, Tickable
from .module import ClockProvider

__all__ = ["Clock", "ClockProvider", "Fixed", "Real", "Tickable"]
