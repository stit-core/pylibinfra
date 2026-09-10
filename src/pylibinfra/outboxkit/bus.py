"""Insert-сторона outbox'а: `Bus` сериализует payload и пишет событие в `Repository`."""

from __future__ import annotations

import json
from typing import Any, Protocol

from .errors import OutboxError
from .metrics import OutboxInstruments
from .outbox import Repository


class Publisher(Protocol):
    """Узкий порт публикации для доменного кода и тестов."""

    async def publish(self, subject: str, payload: Any) -> None:
        """Кладёт событие в outbox — внутри той же транзакции, что и доменная запись."""


class Bus:
    """Сериализует payload в JSON и вставляет событие в `Repository`.

    Вызов вне транзакции теряет атомарность факта и его публикации; при
    `OUTBOX_REQUIRE_TX=true` хранилище отвергает такой insert (`NoTxError`).
    """

    def __init__(
        self, instance: str, repo: Repository, instr: OutboxInstruments | None = None
    ) -> None:
        self._repo = repo
        self._instr = instr or OutboxInstruments(instance)

    async def publish(self, subject: str, payload: Any) -> None:
        """Вставляет событие в outbox; несериализуемый payload — `OutboxError` до похода в БД."""
        try:
            data = encode_payload(payload)
        except OutboxError:
            self._inserted(subject, "marshal_error")
            raise
        try:
            await self._repo.insert(subject, data)
        except Exception:
            self._inserted(subject, "error")
            raise
        self._inserted(subject, "ok")

    def _inserted(self, subject: str, status: str) -> None:
        self._instr.inserted_total.add(
            1, {**self._instr.attrs, "subject": subject, "status": status}
        )


def encode_payload(payload: Any) -> bytes:
    """Сериализует payload события в компактный JSON; несериализуемое значение — `OutboxError`."""
    if isinstance(payload, bytes | bytearray):
        return bytes(payload)
    try:
        return json.dumps(payload, separators=(",", ":")).encode()
    except (TypeError, ValueError) as exc:
        raise OutboxError(f"outboxkit: marshal payload: {exc}") from exc
