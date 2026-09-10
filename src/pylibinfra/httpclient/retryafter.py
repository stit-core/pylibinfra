"""Разбор заголовка `Retry-After` по RFC 9110 — одно правило на всех потребителей."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

# Числовая форма содержит только эти символы: `float()` иначе съел бы "1_0",
# "inf" и "nan", которых в заголовке быть не может.
_NUMERIC = frozenset("0123456789+-.eE")


def parse_retry_after(value: str, now: datetime) -> float | None:
    """Пауза в секундах из значения заголовка: задержка либо HTTP-дата относительно `now`.

    `None` — значение пустое, отрицательное или нечитаемое; дата в прошлом — `0.0`.
    Дробная задержка округляется вверх до целой секунды. Потолок паузы ставит
    вызывающий: разбор возвращает то, что прислал сервер.
    """
    value = value.strip()
    if not value:
        return None
    if _NUMERIC.issuperset(value):
        return _seconds(value)
    return _http_date(value, now)


def _seconds(value: str) -> float | None:
    try:
        parsed = float(value)
    except ValueError:
        return None
    if parsed < 0 or math.isnan(parsed) or math.isinf(parsed):
        return None
    return math.ceil(parsed)


def _http_date(value: str, now: datetime) -> float | None:
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return max((moment - now).total_seconds(), 0.0)
