"""Unit-тесты txkit.Identity/Recorder фейковых Transactor'ов (без Docker).

in_rollback_tx требует реального пула — проверяется в test_integration.py.
"""

from __future__ import annotations

from pylibinfra.pgkit import Transactor
from pylibinfra.testkit.txkit import Identity, Recorder


async def test_identity_passthrough() -> None:
    tx: Transactor = Identity()
    calls = 0

    async def body() -> int:
        nonlocal calls
        calls += 1
        return 42

    assert await tx.with_tx(body) == 42
    assert calls == 1


async def test_recorder_counts_calls() -> None:
    rec = Recorder()
    _: Transactor = rec

    async def body() -> str:
        return "ok"

    assert await rec.with_tx(body) == "ok"
    assert await rec.with_tx(body) == "ok"
    assert rec.calls() == 2
