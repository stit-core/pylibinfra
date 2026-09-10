"""Клиент Redis кита: пул по Config, ретраи, наблюдаемость команды, проверка версии сервера."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import redis.asyncio as redis
from opentelemetry.trace import SpanKind
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialWithJitterBackoff, NoBackoff

from pylibinfra._internal import otelkit

from .config import Config
from .errors import ServerVersionError

_KIT = "cachekit"

# Тегированная инвалидация опирается на `PEXPIRE … GT` (Redis 7.0+): на 6.x
# падает не первая запись в тег, а вторая.
MIN_SERVER_MAJOR = 7

_SKIP_HINT = "set REDIS_SKIP_VERSION_CHECK=true for a non-standard server"


class ObservedRedis(redis.Redis):
    """`redis.asyncio.Redis`, который считает каждую команду в метрики кита и открывает спан.

    Инструменты создаются в конструкторе: клиент, собранный до настройки
    OTel-провайдера, останется no-op на весь свой срок жизни.
    """

    def __init__(self, **kwargs: Any) -> None:
        """Принимает kwargs `redis.asyncio.Redis`; фабрика кита — `new_client`."""
        super().__init__(**kwargs)
        meter = otelkit.meter(_KIT)
        self._commands = meter.create_counter(
            "cache_commands_total",
            description="Redis commands by name and result (ok|miss|error)",
        )
        self._duration = otelkit.duration_histogram(
            meter,
            "cache_command_duration_seconds",
            "Redis command round-trip duration",
        )
        self._tracer = otelkit.tracer(_KIT)

    async def execute_command(self, *args: Any, **options: Any) -> Any:
        """Команда под спаном `redis <cmd>`; nil-ответ — `result=miss`, отказ — `error`."""
        cmd = command_name(args)
        started = time.perf_counter()
        # Ключи и значения в атрибуты не кладём: PII и неограниченная кардинальность.
        with self._tracer.start_as_current_span(
            f"redis {cmd}",
            kind=SpanKind.CLIENT,
            attributes={"db.system": "redis", "db.operation": cmd},
        ):
            try:
                response = await super().execute_command(*args, **options)  # type: ignore[no-untyped-call]
            except BaseException:
                self._record(cmd, started, "error")
                raise
            self._record(cmd, started, "miss" if response is None else "ok")
            return response

    def _record(self, cmd: str, started: float, result: str) -> None:
        self._commands.add(1, {"cmd": cmd, "result": result})
        self._duration.record(time.perf_counter() - started, {"cmd": cmd})


def command_name(args: tuple[Any, ...]) -> str:
    """Имя команды для label'а `cmd`: первое слово в нижнем регистре (`script load` → `script`)."""
    if not args:
        return "unknown"
    head = args[0]
    if isinstance(head, bytes):
        head = head.decode("utf-8", "replace")
    return str(head).split(" ", 1)[0].lower()


def new_client(cfg: Config) -> redis.Redis:
    """Собирает клиент Redis по конфигу; соединение открывается на первой команде."""
    return ObservedRedis.from_url(
        cfg.url.get_secret_value(),
        max_connections=cfg.pool_size,
        socket_connect_timeout=_timeout(cfg.dial_timeout),
        socket_timeout=_timeout(cfg.read_timeout),
        retry=_retry(cfg.max_retries),
    )


def _timeout(seconds: float) -> float | None:
    """`0` — ждать без ограничения: нулевой дедлайн redis-py трактует как «уже истёк»."""
    return seconds if seconds > 0 else None


def _retry(max_retries: int) -> Retry:
    """Политика повторов; `-1` и `0` — без повторов: отрицательное redis-py читает как «вечно»."""
    if max_retries <= 0:
        return Retry(NoBackoff(), retries=0)
    return Retry(ExponentialWithJitterBackoff(), retries=max_retries)


async def check_server_version(client: redis.Redis) -> None:
    """Читает `INFO server` и валит старт `ServerVersionError` на сервере старше Redis 7."""
    info = await client.info("server")
    check_info_version(info)


def check_info_version(info: Mapping[str, Any]) -> None:
    """Проверяет `redis_version` ответа `INFO`; нет поля или нечитаемо — `ServerVersionError`."""
    version = str(info.get("redis_version", "")).strip()
    if not version:
        raise ServerVersionError(f"cachekit: INFO server reports no redis_version; {_SKIP_HINT}")
    head = version.split(".", 1)[0]
    if not head.isdigit():
        raise ServerVersionError(
            f"cachekit: cannot read the major version of {version!r}; {_SKIP_HINT}"
        )
    if int(head) < MIN_SERVER_MAJOR:
        raise ServerVersionError(
            f"cachekit: server reports {version}, cachekit needs >= {MIN_SERVER_MAJOR}.0"
            " (tagged invalidation uses PEXPIRE … GT)"
        )
