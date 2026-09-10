"""Unit-тесты фейковых Transactor'ов pgtest (без Docker).

in_rollback_tx требует реального пула — он проверяется в test_postgres.py.
"""

from __future__ import annotations

import pytest

from pylibinfra.pgkit import Transactor, TxOptions, in_tx
from pylibinfra.testkit.pgtest import Identity, Recorder


async def test_identity_passthrough() -> None:
    tx: Transactor = Identity()
    calls = 0

    async def body() -> int:
        nonlocal calls
        calls += 1
        return 42

    assert await tx.with_tx(body) == 42
    assert calls == 1


async def test_identity_marks_context_as_in_tx() -> None:
    tx = Identity()
    seen: list[bool] = []

    async def body() -> None:
        seen.append(in_tx())

    await tx.with_tx(body)
    assert seen == [True]
    assert in_tx() is False


async def test_identity_accepts_options() -> None:
    async def body() -> str:
        return "ok"

    assert await Identity().with_tx(body, options=TxOptions(read_only=True)) == "ok"


async def test_recorder_counts_calls_and_marks_context() -> None:
    rec = Recorder()
    _: Transactor = rec
    seen: list[bool] = []

    async def body() -> str:
        seen.append(in_tx())
        return "ok"

    assert await rec.with_tx(body) == "ok"
    assert await rec.with_tx(body) == "ok"
    assert rec.calls() == 2
    assert seen == [True, True]
    assert in_tx() is False


async def test_recorder_resets_marker_on_error() -> None:
    rec = Recorder()

    async def body() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await rec.with_tx(body)
    assert in_tx() is False
    assert rec.calls() == 1
