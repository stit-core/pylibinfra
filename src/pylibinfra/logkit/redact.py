"""PII-redact: маски по `msg` и строковым значениям полей до fan-out'а в оба sink'а.

Это страховка, а не замена дисциплине «не логировать PII»: набор — эвристики,
полноты они не гарантируют.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

DEFAULT_REPLACE_WITH = "[REDACTED]"

# «Bearer <jwt>» схлопывается целиком, поэтому схемы авторизации стоят раньше JWT.
DEFAULT_PATTERNS: tuple[str, ...] = (
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
    r"\+\d{10,15}\b|\b[78]\d{10}\b",
    r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",
    r"(?i:\bbearer\s+[A-Za-z0-9\-._~+/]+=*)",
    r"(?i:\bbasic\s+[A-Za-z0-9+/]{8,}={0,2})",
    r"\beyJ[A-Za-z0-9_-]{6,}\.eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]+",
    r"\bsk-ant-[A-Za-z0-9_-]{16,}",
    r"\bsk-[A-Za-z0-9]{20,}\b",
)

# 11 = длина самого короткого «цифрового» дефолта (телефон; карта — 16 с разделителями).
_MIN_DIGITS = 11
_NUM_SEP = "- \t\n\v\f\r"


class Redactor:
    """Скомпилированный набор масок; применяется к тексту и к значениям полей."""

    __slots__ = ("_hint", "_patterns", "_with")

    def __init__(self, patterns: Sequence[re.Pattern[str]], replace_with: str, *, hint: bool):
        """`hint` включает литеральный pre-filter — он выведен из дефолтного набора."""
        self._patterns = tuple(patterns)
        self._with = replace_with
        self._hint = hint

    def text(self, value: str) -> str:
        """Маскирует совпадения в строке; без совпадений возвращает её же."""
        if self._hint and not default_hint(value):
            return value
        for pattern in self._patterns:
            value = pattern.sub(self._with, value)
        return value

    def value(self, value: Any) -> Any:
        """Маскирует строки внутри значения поля; числа, `bool` и `None` не трогает."""
        if value is None or isinstance(value, bool | int | float):
            return value
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, Mapping):
            return {key: self.value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.value(item) for item in value)
        if isinstance(value, BaseException):
            return self.text(str(value))
        # Прочий объект уходит в лог строковым представлением — маска работает по нему.
        as_text = str(value)
        masked = self.text(as_text)
        return masked if masked != as_text else value

    def fields(self, fields: Mapping[str, Any]) -> dict[str, Any]:
        """Маскирует значения полей записи, ключи не трогает."""
        return {key: self.value(item) for key, item in fields.items()}


def compile_rules(patterns: Sequence[str], replace_with: str) -> Redactor:
    """Компилирует набор масок (пустой — дефолтный); невалидный набор — `ValueError`."""
    for char in replace_with:
        if char in '"\\' or not char.isprintable():
            raise ValueError(
                f"LOG_REDACT_REPLACE_WITH {replace_with!r}: {char!r} breaks the JSON "
                "encoding of redacted values"
            )
    source = tuple(patterns)
    hint = not source
    if hint:
        source = DEFAULT_PATTERNS
    # Дефолтный набор — только ASCII: иначе `\d`/`\s`/`\b` ловят юникод-пробелы и
    # цифры, которых pre-filter не знает, и маска молча не срабатывает.
    flags = re.ASCII if hint else re.NOFLAG
    compiled: list[re.Pattern[str]] = []
    for pattern in source:
        try:
            expr = re.compile(pattern, flags)
        except re.error as exc:
            raise ValueError(f"LOG_REDACT_PATTERNS {pattern!r}: {exc}") from None
        # Совпадение с пустой строкой замаскировало бы каждую позицию записи.
        if expr.search("") is not None:
            raise ValueError(f"LOG_REDACT_PATTERNS {pattern!r}: pattern matches the empty string")
        compiled.append(expr)
    return Redactor(compiled, replace_with or DEFAULT_REPLACE_WITH, hint=hint)


def default_hint(value: str) -> bool:
    """Необходимое условие совпадения дефолтного набора — скан по символам вместо regex'а."""
    run = 0
    separated = False
    for i, char in enumerate(value):
        if char in "@+":
            return True
        if "0" <= char <= "9":
            run += 1
            separated = False
            if run >= _MIN_DIGITS:
                return True
            continue
        if run > 0 and not separated and char in _NUM_SEP:
            separated = True
            continue
        lowered = char.lower()
        if lowered == "b" and (
            value[i : i + 6].lower() == "bearer" or value[i : i + 5].lower() == "basic"
        ):
            return True
        if char == "e" and value.startswith("eyJ", i):
            return True
        if char == "s" and value.startswith("sk-", i):
            return True
        run, separated = 0, False
    return False
