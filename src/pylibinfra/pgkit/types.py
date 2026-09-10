"""Тип-алиасы поверх asyncpg и конвертеры значений.

Единая точка, чтобы потребитель не тянул asyncpg напрямую ради базовых сигнатур.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]

from .errors import InvalidUUIDError

Record = asyncpg.Record
Connection = asyncpg.Connection
Pool = asyncpg.Pool


def parse_uuid(s: str) -> UUID:
    """Разбирает строку в UUID; пустая или неразбираемая строка — `InvalidUUIDError`."""
    if not s:
        raise InvalidUUIDError("pgkit: empty uuid")
    try:
        return UUID(s)
    except ValueError as exc:
        raise InvalidUUIDError(f"pgkit: invalid uuid: {s!r}") from exc


def unmarshal_jsonb(raw: str | bytes | None) -> Any:
    """Разворачивает значение jsonb-колонки; NULL и пустая строка дают `None`.

    Кит не ставит на пул json/jsonb-кодек: asyncpg отдаёт такие колонки текстом,
    и это единственная форма, в которой их видят все pgstore библиотеки.
    """
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"pgkit: unmarshal jsonb: {exc}") from exc
