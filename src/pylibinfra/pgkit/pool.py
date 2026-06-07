"""PoolConfig + new_pool: создаёт asyncpg-пул с размерами/lifetime из конфига,
выставляет session-таймауты на каждом новом соединении и опционально
инструментирует per-query спанами через otel-asyncpg."""

import asyncpg  # type: ignore[import-untyped]
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .types import Connection, Pool

_INSTRUMENTED = False


class PoolConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    dsn: str = Field(validation_alias="PG_DSN")
    max_conns: int = Field(default=10, validation_alias="PG_MAX_CONNS")
    min_conns: int = Field(default=2, validation_alias="PG_MIN_CONNS")
    # секунды
    max_conn_idle_time: float = Field(default=300.0, validation_alias="PG_MAX_CONN_IDLE_TIME")
    otel_trace: bool = Field(default=True, validation_alias="PG_OTEL_TRACE")

    statement_timeout: float = Field(default=30.0, validation_alias="PG_STATEMENT_TIMEOUT")
    idle_in_tx_session_timeout: float = Field(
        default=60.0, validation_alias="PG_IDLE_IN_TX_SESSION_TIMEOUT"
    )
    lock_timeout: float = Field(default=5.0, validation_alias="PG_LOCK_TIMEOUT")


def _ms(seconds: float) -> int:
    ms = int(seconds * 1000)
    return ms if ms > 0 else 0


async def _apply_session_timeouts(conn: Connection, cfg: PoolConfig) -> None:
    settings = (
        ("statement_timeout", cfg.statement_timeout),
        ("idle_in_transaction_session_timeout", cfg.idle_in_tx_session_timeout),
        ("lock_timeout", cfg.lock_timeout),
    )
    for name, dur in settings:
        await conn.execute(f"SET {name} = '{_ms(dur)}ms'")


def _instrument_queries() -> None:
    # otel-asyncpg патчит модуль глобально; повторный instrument() кидает.
    global _INSTRUMENTED
    if _INSTRUMENTED:
        return
    from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor

    AsyncPGInstrumentor().instrument()  # type: ignore[no-untyped-call]
    _INSTRUMENTED = True


async def new_pool(cfg: PoolConfig) -> Pool:
    if cfg.otel_trace:
        _instrument_queries()

    async def _init(conn: Connection) -> None:
        await _apply_session_timeouts(conn, cfg)

    pool: Pool = await asyncpg.create_pool(
        dsn=cfg.dsn,
        min_size=cfg.min_conns,
        max_size=cfg.max_conns,
        max_inactive_connection_lifetime=cfg.max_conn_idle_time,
        init=_init,
    )
    return pool
