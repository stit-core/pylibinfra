"""Публичный env-контракт шины: `Config` и вложенные `BackpressureConfig`/`JetStreamConfig`.

Длительности — секунды (float). `new_config()` читает голые `NATS_*`,
`new_config_prefixed(name)` — только `<NAME>_NATS_*`: наборы переменных инстансов
изолированы, незаданная префиксная переменная берёт дефолт кита, а не голую `NATS_*`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode

from pylibinfra._internal import configkit
from pylibinfra._internal.configkit import KitSettings

from .errors import ConfigError
from .subjects import valid_instance_name, valid_publish_subject, valid_stream_name

_KIT = "natskit"

DEFAULT_INSTANCE = "default"
"""Имя дефолтного (безымянного) инстанса в метриках, спанах и логах."""

DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_MAX_IN_FLIGHT = 100
DEFAULT_DRAIN_TIMEOUT = 10.0
DEFAULT_ACK_WAIT = 30.0
DEFAULT_MAX_AGE = 7 * 24 * 3600.0
DEFAULT_DUPLICATES = 120.0
DEFAULT_PUBLISH_ASYNC_TIMEOUT = 5.0

# Ниже этого порога in_progress() не успевает продлить ожидание сервера.
MIN_ACK_WAIT = 1.0

_ALLOWED_SCHEMES = frozenset({"nats", "tls", "ws", "wss"})
_RETENTION_POLICIES = frozenset({"limits", "interest", "workqueue", "work_queue"})

# В позиции пользователя живёт и токен, поэтому имя пользователя тоже скрывается.
_REDACTED_USERINFO = "xxxxx"


def redact_url(raw: str) -> str:
    """Прячет userinfo в URL (или в списке URL через запятую): `nats://xxxxx@host:4222`."""
    parts = raw.split(",")
    for i, part in enumerate(parts):
        scheme, sep, rest = part.strip().partition("://")
        if not sep:
            continue
        at = rest.rfind("@")
        if at < 0:
            continue
        parts[i] = f"{scheme}://{_REDACTED_USERINFO}@{rest[at + 1 :]}"
    return ",".join(parts)


def positive_or(value: float, default: float) -> float:
    """Значение, если оно положительное, иначе дефолт кита (`0` в ENV = «дефолт»)."""
    return value if value > 0 else default


def positive_int_or(value: int, default: int) -> int:
    """Целое, если оно положительное, иначе дефолт кита."""
    return value if value > 0 else default


def env_prefix(instance: str) -> str:
    """ENV-префикс инстанса: `hub` → `HUB` (`-`, `.` и пробел становятся `_`)."""
    out = instance.upper()
    for ch in "-. ":
        out = out.replace(ch, "_")
    return out


def _split_csv(value: Any) -> Any:
    if isinstance(value, str):
        return tuple(p.strip() for p in value.split(",") if p.strip())
    return value


class BackpressureConfig(KitSettings):
    """Потолок одновременно обрабатываемых Core-сообщений (вес семафора `Bus`)."""

    max_in_flight: int = Field(
        default=DEFAULT_MAX_IN_FLIGHT, ge=0, validation_alias="NATS_MAX_IN_FLIGHT"
    )


class JetStreamConfig(KitSettings):
    """Включение JetStream, декларация стримов и poison-обработка консьюмера."""

    enabled: bool = Field(default=False, validation_alias="NATS_JETSTREAM_ENABLED")
    stream_names: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(), validation_alias="NATS_JS_STREAM_NAMES"
    )
    stream_managed: bool = Field(default=True, validation_alias="NATS_JS_STREAM_MANAGED")
    retention: str = Field(default="limits", validation_alias="NATS_JS_RETENTION")
    max_age: float = Field(default=DEFAULT_MAX_AGE, ge=0, validation_alias="NATS_JS_MAX_AGE")
    duplicates: float = Field(
        default=DEFAULT_DUPLICATES, ge=0, validation_alias="NATS_JS_DUPLICATES"
    )
    max_in_flight: int = Field(
        default=DEFAULT_MAX_IN_FLIGHT, ge=0, validation_alias="NATS_JS_MAX_IN_FLIGHT"
    )
    ack_wait: float = Field(default=DEFAULT_ACK_WAIT, ge=0, validation_alias="NATS_JS_ACK_WAIT")
    handler_timeout: float = Field(default=0.0, ge=0, validation_alias="NATS_JS_HANDLER_TIMEOUT")
    publish_async_timeout: float = Field(
        default=DEFAULT_PUBLISH_ASYNC_TIMEOUT,
        ge=0,
        validation_alias="NATS_JS_PUBLISH_ASYNC_TIMEOUT",
    )
    max_deliver: int = Field(default=10, ge=0, validation_alias="NATS_JS_MAX_DELIVER")
    nak_backoff: float = Field(default=1.0, ge=0, validation_alias="NATS_JS_NAK_BACKOFF")
    dlq_subject: str = Field(default="", validation_alias="NATS_JS_DLQ_SUBJECT")

    @field_validator("stream_names", mode="before")
    @classmethod
    def _split_names(cls, value: Any) -> Any:
        return _split_csv(value)

    @field_validator("stream_names")
    @classmethod
    def _valid_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for name in value:
            if not valid_stream_name(name):
                raise ValueError(f"NATS_JS_STREAM_NAMES: {name!r} is not a valid stream name")
        return value

    @field_validator("retention")
    @classmethod
    def _valid_retention(cls, value: str) -> str:
        key = value.strip().lower()
        if key and key not in _RETENTION_POLICIES:
            raise ValueError(f"NATS_JS_RETENTION: unknown retention policy {value!r}")
        return key or "limits"

    @field_validator("ack_wait")
    @classmethod
    def _valid_ack_wait(cls, value: float) -> float:
        if 0 < value < MIN_ACK_WAIT:
            raise ValueError(
                f"NATS_JS_ACK_WAIT must be 0 (default {DEFAULT_ACK_WAIT:g}s) "
                f"or >= {MIN_ACK_WAIT:g}s, got {value:g}s"
            )
        return value

    @field_validator("dlq_subject")
    @classmethod
    def _valid_dlq(cls, value: str) -> str:
        if value and not valid_publish_subject(value):
            raise ValueError(f"NATS_JS_DLQ_SUBJECT: {value!r} is not a valid publish subject")
        return value


class Config(KitSettings):
    """Соединение, аутентификация и вложенные секции инстанса шины."""

    url: str = Field(default="nats://localhost:4222", validation_alias="NATS_URL")
    connect_timeout: float = Field(
        default=DEFAULT_CONNECT_TIMEOUT, ge=0, validation_alias="NATS_CONNECT_TIMEOUT"
    )
    reconnect_wait: float = Field(default=2.0, ge=0, validation_alias="NATS_RECONNECT_WAIT")
    max_reconnects: int = Field(default=-1, ge=-1, validation_alias="NATS_MAX_RECONNECTS")
    drain_timeout: float = Field(
        default=DEFAULT_DRAIN_TIMEOUT, ge=0, validation_alias="NATS_DRAIN_TIMEOUT"
    )
    handler_timeout: float = Field(default=30.0, ge=0, validation_alias="NATS_HANDLER_TIMEOUT")
    creds_file: str = Field(default="", validation_alias="NATS_CREDS_FILE")
    nkey_seed_file: str = Field(default="", validation_alias="NATS_NKEY_SEED_FILE")
    tls_ca_file: str = Field(default="", validation_alias="NATS_TLS_CA_FILE")
    backpressure: BackpressureConfig = Field(default_factory=BackpressureConfig)
    jetstream: JetStreamConfig = Field(default_factory=JetStreamConfig)

    @field_validator("url")
    @classmethod
    def _valid_url(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("NATS_URL is empty")
        shown = redact_url(value)
        for part in value.split(","):
            entry = part.strip()
            if not entry:
                raise ValueError(f"NATS_URL {shown!r} has an empty entry")
            # nats-py подставляет схему сама; без неё urlsplit принял бы host за схему.
            if "://" not in entry:
                entry = f"nats://{entry}"
            parsed = urlsplit(entry)
            if parsed.scheme not in _ALLOWED_SCHEMES:
                raise ValueError(
                    f"NATS_URL {shown!r}: unsupported scheme {parsed.scheme!r} (nats, tls, ws, wss)"
                )
            if not parsed.hostname:
                raise ValueError(f"NATS_URL {shown!r}: host is empty")
        return value

    @model_validator(mode="after")
    def _auth_files_exist(self) -> Config:
        for env, path in (
            ("NATS_CREDS_FILE", self.creds_file),
            ("NATS_NKEY_SEED_FILE", self.nkey_seed_file),
            ("NATS_TLS_CA_FILE", self.tls_ca_file),
        ):
            if path and not Path(path).is_file():
                raise ValueError(f"{env}: {path!r}: no such file")
        return self


def new_config() -> Config:
    """Читает и валидирует конфиг дефолтного инстанса из голых `NATS_*`."""
    return configkit.build(Config, _KIT, ConfigError)


def new_config_prefixed(name: str) -> Config:
    """Читает конфиг именованного инстанса из `<NAME>_NATS_*`; голые `NATS_*` игнорируются."""
    if not name or not valid_instance_name(name):
        raise ConfigError(
            f"{_KIT}: invalid config: instance {name!r}: "
            "allowed characters are letters, digits, '_', '-', '.'"
        )
    return _build(_field_values(Config, env_prefix(name)), {})


def config_without_env(**values: Any) -> Config:
    """Конфиг из дефолтов кита и явных значений; ENV не читается — для тестов и фейков.

    Вложенные секции задаются словарями (`jetstream={"enabled": True}`); поле
    вложенной секции, переданное верхним уровнем, — `ConfigError`, а не тихий дефолт.
    """
    _check_fields(Config, values, "")
    return _build(_field_values(Config, None), values)


def _check_fields(model: type[BaseSettings], values: dict[str, Any], path: str) -> None:
    for key, value in values.items():
        field = model.model_fields.get(key)
        if field is None:
            raise ConfigError(f"{_KIT}: invalid config: unknown field {path + key!r}")
        annotation = field.annotation
        if (
            isinstance(value, dict)
            and isinstance(annotation, type)
            and issubclass(annotation, BaseSettings)
        ):
            _check_fields(annotation, value, f"{path}{key}.")


def _build(defaults: dict[str, Any], overrides: dict[str, Any]) -> Config:
    merged = dict(defaults)
    for key, value in overrides.items():
        base = merged.get(key)
        merged[key] = (
            {**base, **value} if isinstance(value, dict) and isinstance(base, dict) else value
        )
    try:
        return Config.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(configkit.describe(_KIT, exc)) from None


def _field_values(model: type[BaseSettings], prefix: str | None) -> dict[str, Any]:
    """Значения всех полей модели: из `<PREFIX>_<ENV>` (или только дефолты при `prefix is None`).

    Заполняются и незаданные поля: без явного значения pydantic-settings подхватил
    бы голую `NATS_*`, и именованный инстанс молча унаследовал бы чужую шину.
    """
    values: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseSettings):
            values[name] = _field_values(annotation, prefix)
            continue
        alias = field.validation_alias
        if not isinstance(alias, str):
            continue
        raw = os.environ.get(f"{prefix}_{alias}") if prefix is not None else None
        values[name] = raw if raw else field.get_default(call_default_factory=True)
    return values
