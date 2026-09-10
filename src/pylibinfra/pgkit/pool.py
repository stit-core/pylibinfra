"""PoolConfig + new_pool: asyncpg-пул с размерами, таймаутом коннекта и
сессионными таймаутами из конфига, проверенный стартовым ping'ом."""

from __future__ import annotations

import asyncio
from typing import Any, Self

import asyncpg  # type: ignore[import-untyped]
from pydantic import Field, SecretStr, model_validator

from pylibinfra._internal import configkit
from pylibinfra._internal.configkit import KitSettings

from .dsn import safe_error
from .errors import ConfigError, ConnectError
from .types import Pool

_INSTRUMENTED = False


class PoolConfig(KitSettings):
    """Параметры пула primary Postgres; читается из ENV через `new_pool_config`.

    Нулевой размер пула означает «дефолт asyncpg», а не «выключено»: конфиг с
    одним `PG_DSN` обязан давать рабочий пул.
    """

    model_config = configkit.settings_config()

    dsn: SecretStr = Field(validation_alias="PG_DSN")
    max_conns: int = Field(default=10, validation_alias="PG_MAX_CONNS")
    min_conns: int = Field(default=2, validation_alias="PG_MIN_CONNS")
    max_conn_idle_time: float = Field(default=300.0, validation_alias="PG_MAX_CONN_IDLE_TIME")
    connect_timeout: float = Field(default=10.0, validation_alias="PG_CONNECT_TIMEOUT")
    otel_trace: bool = Field(default=True, validation_alias="PG_OTEL_TRACE")

    statement_timeout: float = Field(default=30.0, validation_alias="PG_STATEMENT_TIMEOUT")
    idle_in_tx_session_timeout: float = Field(
        default=60.0, validation_alias="PG_IDLE_IN_TX_SESSION_TIMEOUT"
    )
    lock_timeout: float = Field(default=5.0, validation_alias="PG_LOCK_TIMEOUT")

    @model_validator(mode="after")
    def _check(self) -> Self:
        """Проверяет инварианты; текст ошибки называет ENV-переменную, а не поле."""
        if not self.dsn.get_secret_value():
            raise ValueError("PG_DSN is empty")
        sizes = (("PG_MAX_CONNS", self.max_conns), ("PG_MIN_CONNS", self.min_conns))
        for env, size in sizes:
            if size < 0:
                raise ValueError(f"{env} must be >= 0, got {size}")
        if self.max_conns > 0 and self.min_conns > self.max_conns:
            raise ValueError(
                f"PG_MIN_CONNS ({self.min_conns}) must be <= PG_MAX_CONNS ({self.max_conns})"
            )
        durations = (
            ("PG_MAX_CONN_IDLE_TIME", self.max_conn_idle_time),
            ("PG_CONNECT_TIMEOUT", self.connect_timeout),
            ("PG_STATEMENT_TIMEOUT", self.statement_timeout),
            ("PG_IDLE_IN_TX_SESSION_TIMEOUT", self.idle_in_tx_session_timeout),
            ("PG_LOCK_TIMEOUT", self.lock_timeout),
        )
        for env, seconds in durations:
            if seconds < 0:
                raise ValueError(f"{env} must be >= 0, got {seconds:g}")
        return self

    def server_settings(self) -> dict[str, str]:
        """Сессионные таймауты в миллисекундах для StartupMessage соединения."""
        return {
            "statement_timeout": _ms(self.statement_timeout),
            "idle_in_transaction_session_timeout": _ms(self.idle_in_tx_session_timeout),
            "lock_timeout": _ms(self.lock_timeout),
        }


def new_pool_config() -> PoolConfig:
    """Читает `PoolConfig` из ENV; нарушение инварианта — `pgkit.ConfigError`."""
    return configkit.build(PoolConfig, "pgkit", ConfigError)


def _ms(seconds: float) -> str:
    return str(max(int(seconds * 1000), 0))


def _instrument_queries() -> None:
    # otel-asyncpg патчит модуль глобально; повторный instrument() кидает.
    global _INSTRUMENTED
    if _INSTRUMENTED:
        return
    from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor

    AsyncPGInstrumentor().instrument()  # type: ignore[no-untyped-call]
    _INSTRUMENTED = True


async def new_pool(cfg: PoolConfig) -> Pool:
    """Открывает пул и проверяет его `SELECT 1` под `PG_CONNECT_TIMEOUT`.

    Сбой коннекта или ping'а — `pgkit.ConnectError`; неразбираемый `PG_DSN` и
    несовместимые размеры пула (`PG_MIN_CONNS` не помещается в дефолтный
    `PG_MAX_CONNS=0`) — `pgkit.ConfigError`. Пароль из DSN в текст ошибки не
    попадает.
    """
    if cfg.otel_trace:
        _instrument_queries()

    dsn = cfg.dsn.get_secret_value()
    kwargs: dict[str, Any] = {"server_settings": cfg.server_settings()}
    # Ноль означает «оставить дефолт asyncpg»: перенесённый в пул, он его ломает
    # (max_size=0 отвергается, max_inactive_connection_lifetime=0 — без ротации).
    if cfg.max_conns > 0:
        kwargs["max_size"] = cfg.max_conns
    if cfg.min_conns > 0:
        kwargs["min_size"] = cfg.min_conns
    if cfg.max_conn_idle_time > 0:
        kwargs["max_inactive_connection_lifetime"] = cfg.max_conn_idle_time
    if cfg.connect_timeout > 0:
        kwargs["timeout"] = cfg.connect_timeout

    try:
        pool: Pool = await asyncpg.create_pool(dsn=dsn, **kwargs)
    except asyncpg.exceptions.ClientConfigurationError as exc:
        raise ConfigError(f"pgkit: invalid config: PG_DSN: {safe_error(dsn, exc)}") from exc
    except ValueError as exc:
        raise ConfigError(f"pgkit: invalid config: pool sizes: {safe_error(dsn, exc)}") from exc
    except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
        raise ConnectError(f"pgkit: connect: {safe_error(dsn, exc)}") from exc

    timeout = cfg.connect_timeout if cfg.connect_timeout > 0 else None
    try:
        async with asyncio.timeout(timeout):
            await pool.fetchval("SELECT 1")
    except TimeoutError as exc:
        # У TimeoutError пустой текст: без явного сообщения ошибка звучала бы
        # как «pgkit: ping: » и не называла бы сорванный дедлайн.
        pool.terminate()
        deadline = f" after {cfg.connect_timeout:g}s" if timeout is not None else ""
        raise ConnectError(f"pgkit: ping: timed out{deadline}") from exc
    except Exception as exc:
        # terminate, а не close: graceful-закрытие ждёт возврата соединений и на
        # неотвечающем сервере растянуло бы падение старта на минуту.
        pool.terminate()
        raise ConnectError(f"pgkit: ping: {safe_error(dsn, exc)}") from exc
    return pool
