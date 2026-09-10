"""UUID v7 с монотонным sub-ms счётчиком и проверка канонического вида."""

from __future__ import annotations

import os
import threading
import time
import uuid


class InvalidIDError(ValueError):
    """Строка не является каноническим UUID."""

    def __init__(self, value: str) -> None:
        super().__init__(f"idkit: invalid uuid: {value!r}")
        self.value = value


_lock = threading.Lock()
_last_ms = 0
_seq = 0
_SEQ_MAX = 0xFFF
_RAND_B_MASK = (1 << 62) - 1


def new_v7() -> str:
    """Новый UUID v7 строкой; в пределах процесса значения строго возрастают."""
    global _last_ms, _seq
    with _lock:
        now_ms = time.time_ns() // 1_000_000
        if now_ms > _last_ms:
            _last_ms, _seq = now_ms, 0
        else:
            # Та же (или откатившаяся) миллисекунда: rand_a работает счётчиком,
            # переполнение сдвигает метку на 1 мс — порядок выдачи сохраняется.
            _seq += 1
            if _seq > _SEQ_MAX:
                _last_ms, _seq = _last_ms + 1, 0
        ms, seq = _last_ms, _seq
    rand_b = int.from_bytes(os.urandom(8), "big") & _RAND_B_MASK
    value = ((ms & 0xFFFFFFFFFFFF) << 80) | (0x7 << 76) | (seq << 64) | (0b10 << 62) | rand_b
    return str(uuid.UUID(int=value))


def validate(value: str) -> None:
    """Принимает только канонический вид — 36 символов, lowercase hex, 8-4-4-4-12."""
    _parse_canonical(value)


def is_v7(value: str) -> bool:
    """True, если `value` — канонический UUID версии 7 с вариантом RFC 4122."""
    try:
        parsed = _parse_canonical(value)
    except InvalidIDError:
        return False
    return parsed.version == 7 and parsed.variant == uuid.RFC_4122


def _parse_canonical(value: str) -> uuid.UUID:
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise InvalidIDError(value) from None
    # uuid.UUID принимает URN, фигурные скобки, 32 hex без дефисов и верхний
    # регистр — канонический вид ровно один, остальное отвергаем.
    if str(parsed) != value:
        raise InvalidIDError(value)
    return parsed
