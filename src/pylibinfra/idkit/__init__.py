"""idkit — внутренние идентификаторы.

`new_v7` — UUID v7 (RFC 9562) строкой: time-sortable, строго возрастающий в
пределах процесса; `validate`/`is_v7` — проверка канонической формы. Без I/O
и без зависимостей сверх stdlib; ничего не эмитит.
"""

from .id import InvalidIDError, is_v7, new_v7, validate

__all__ = ["InvalidIDError", "is_v7", "new_v7", "validate"]
