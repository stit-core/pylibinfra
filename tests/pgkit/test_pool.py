"""Unit-тесты new_pool: маппинг конфига в asyncpg и ошибки старта (без Postgres)."""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
import pytest

from pylibinfra.pgkit import ConfigError, ConnectError, MigrateError, PoolConfig, new_pool
from pylibinfra.pgkit import migrate as migrate_module

_UNREACHABLE = "postgresql://nobody@127.0.0.1:1/none"


class FakePool:
    def __init__(self, error: Exception | None) -> None:
        self.error = error
        self.closed = False

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if self.error is not None:
            raise self.error
        return 1

    def terminate(self) -> None:
        self.closed = True


def _cfg(**overrides: Any) -> PoolConfig:
    values: dict[str, Any] = {"dsn": _UNREACHABLE, "otel_trace": False}
    values.update(overrides)
    return PoolConfig.model_validate(values)


async def test_new_pool_passes_sizes_and_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_create_pool(**kwargs: Any) -> FakePool:
        captured.update(kwargs)
        return FakePool(None)

    monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
    await new_pool(_cfg(max_conns=7, min_conns=3, max_conn_idle_time=11.0, connect_timeout=4.0))

    assert captured["max_size"] == 7
    assert captured["min_size"] == 3
    assert captured["max_inactive_connection_lifetime"] == 11.0
    assert captured["timeout"] == 4.0
    assert captured["server_settings"]["statement_timeout"] == "30000"


async def test_new_pool_omits_zero_values(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_create_pool(**kwargs: Any) -> FakePool:
        captured.update(kwargs)
        return FakePool(None)

    monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
    await new_pool(_cfg(max_conns=0, min_conns=0, max_conn_idle_time=0.0, connect_timeout=0.0))

    for omitted in ("max_size", "min_size", "max_inactive_connection_lifetime", "timeout"):
        assert omitted not in captured


async def test_new_pool_terminates_pool_when_ping_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FakePool(RuntimeError("server went away"))

    async def fake_create_pool(**kwargs: Any) -> FakePool:
        return pool

    monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
    with pytest.raises(ConnectError, match="pgkit: ping: server went away"):
        await new_pool(_cfg())
    # terminate, а не graceful close: сорвавшийся ping не должен ждать сервер.
    assert pool.closed is True


async def test_new_pool_reports_ping_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    class HangingPool(FakePool):
        async def fetchval(self, sql: str, *args: Any) -> Any:
            await asyncio.sleep(3600)

    pool = HangingPool(None)

    async def fake_create_pool(**kwargs: Any) -> FakePool:
        return pool

    monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
    # У TimeoutError пустой текст — сообщение обязано назвать сорванный дедлайн.
    with pytest.raises(ConnectError, match=r"pgkit: ping: timed out after 0\.01s"):
        await new_pool(_cfg(connect_timeout=0.01))
    assert pool.closed is True


async def test_new_pool_reports_bad_dsn_as_config_error() -> None:
    with pytest.raises(ConfigError, match="pgkit: invalid config: PG_DSN"):
        await new_pool(_cfg(dsn="notaurl://user@host/db"))


async def test_new_pool_reports_incompatible_sizes() -> None:
    # max_conns=0 оставляет дефолт asyncpg (10), в который min_conns=20 не влезает.
    with pytest.raises(ConfigError, match="pool sizes"):
        await new_pool(_cfg(max_conns=0, min_conns=20))


async def test_new_pool_wraps_connect_failure() -> None:
    with pytest.raises(ConnectError, match="pgkit: connect"):
        await new_pool(_cfg(connect_timeout=2.0, min_conns=1, max_conns=1))


def test_migrate_reports_unreachable_backend(tmp_path: Any) -> None:
    directory = tmp_path / "mig"
    directory.mkdir()
    (directory / "0001_x.sql").write_text("SELECT 1;")
    src = migrate_module.Source(name="unreachable", path=str(directory))

    with pytest.raises(MigrateError, match="migrate source 'unreachable': connect"):
        migrate_module.migrate_up(_UNREACHABLE, src)


def test_migrate_up_all_reports_unreachable_lock_backend(tmp_path: Any) -> None:
    directory = tmp_path / "mig"
    directory.mkdir()
    (directory / "0001_x.sql").write_text("SELECT 1;")
    src = migrate_module.Source(name="unreachable", path=str(directory))

    with pytest.raises(MigrateError, match="migrate lock: connect"):
        migrate_module.migrate_up_all(_UNREACHABLE, src, timeout=2.0)
