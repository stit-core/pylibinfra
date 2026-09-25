"""Конфиг OTel-bootstrap'а: endpoint, заголовки, тайминги экспорта, идентичность сервиса."""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import unquote, urlsplit, urlunsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import NoDecode

from pylibinfra._internal import configkit
from pylibinfra._internal.configkit import KitSettings

_KIT = "telemetry"

_HEADER_FORBIDDEN = '()<>@,;:\\"/[]?={}'


class ConfigError(configkit.ConfigError):
    """Невалидный конфиг телеметрии: endpoint, заголовок, доля сэмплирования, таймер."""


def parse_headers(value: str) -> dict[str, str]:
    """Разбирает `k1=v1,k2=v2` (значения percent-encoded) в заголовки OTLP-запросов.

    Текст ошибки не показывает саму запись: значением заголовка бывает токен.
    """
    out: dict[str, str] = {}
    for index, pair in enumerate(value.split(",")):
        if not pair.strip():
            continue
        key, sep, raw = pair.partition("=")
        key = key.strip()
        if not sep or not key:
            raise ValueError(
                f"OTEL_EXPORTER_OTLP_HEADERS entry #{index + 1}: "
                "want key=value pairs separated by comma"
            )
        out[canonical_header(key)] = unquote(raw).strip()
    return out


def canonical_header(key: str) -> str:
    """Канонизирует имя заголовка (`authorization` → `Authorization`); невалидное — `ValueError`."""
    if not key or any(char <= " " or char >= "\x7f" or char in _HEADER_FORBIDDEN for char in key):
        raise ValueError(f"OTEL_EXPORTER_OTLP_HEADERS: {key!r} is not a valid header name")
    return "-".join(part.capitalize() for part in key.split("-"))


def signal_url(endpoint: str, signal: str) -> str:
    """`<endpoint>/v1/<signal>` по спецификации OTel: путь базового URL сохраняется.

    В тексте ошибки userinfo редактируется — в нём бывает пароль.
    """
    parts = urlsplit(endpoint)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(
            f"OTEL_EXPORTER_OTLP_ENDPOINT {redact_userinfo(endpoint)!r}: "
            "want a full URL like http(s)://host:port"
        )
    path = parts.path.rstrip("/") + "/" + signal
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def redact_userinfo(url: str) -> str:
    """Заменяет `user:password@` в URL на `<redacted>@` — для текстов ошибок и логов."""
    head, sep, tail = url.rpartition("@")
    if not sep:
        return url
    scheme, delim, _ = head.partition("://")
    return f"{scheme}{delim}<redacted>@{tail}" if delim else f"<redacted>@{tail}"


class Config(KitSettings):
    """Конфигурация bootstrap'а SDK; поля и env-alias'ы — часть публичного API."""

    model_config = configkit.settings_config()

    # Propagator ставится и при disabled: сквозной trace-контекст от экспорта не зависит.
    disabled: bool = Field(default=False, validation_alias="OTEL_SDK_DISABLED")

    service_name: str = Field(default="", validation_alias="OTEL_SERVICE_NAME")
    service_version: str = Field(default="dev", validation_alias="OTEL_SERVICE_VERSION")
    environment: str = Field(default="dev", validation_alias="APP_ENV")

    otlp_endpoint: str = Field(
        default="http://localhost:4318", validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    sample_ratio: float = Field(
        default=1.0, ge=0.0, le=1.0, validation_alias="OTEL_TRACES_SAMPLER_ARG"
    )

    export_timeout: float = Field(default=10.0, gt=0, validation_alias="OTEL_EXPORT_TIMEOUT")
    trace_batch_delay: float = Field(default=5.0, gt=0, validation_alias="OTEL_TRACES_BATCH_DELAY")
    metric_interval: float = Field(default=10.0, gt=0, validation_alias="OTEL_METRICS_INTERVAL")
    log_batch_delay: float = Field(default=1.0, gt=0, validation_alias="OTEL_LOGS_BATCH_DELAY")

    # Непустой tenant выставляет resource-атрибуты service.namespace и tenant.
    tenant: str = Field(default="", validation_alias="OTEL_TENANT")

    # repr=False: в заголовках бывает Authorization с токеном коллектора.
    otlp_headers: Annotated[dict[str, str], NoDecode] = Field(
        default_factory=dict, validation_alias="OTEL_EXPORTER_OTLP_HEADERS", repr=False
    )
    otlp_bearer_token: SecretStr = Field(default=SecretStr(""), validation_alias="OTEL_TOKEN")

    @field_validator("otlp_headers", mode="before")
    @classmethod
    def _parse_headers(cls, value: Any) -> Any:
        if isinstance(value, str):
            return parse_headers(value)
        if isinstance(value, dict):
            return {canonical_header(str(k)): str(v) for k, v in value.items()}
        return value

    @field_validator("otlp_endpoint")
    @classmethod
    def _full_url(cls, value: str) -> str:
        signal_url(value, "v1/traces")
        return value

    @model_validator(mode="after")
    def _service_name_required(self) -> Config:
        if not self.disabled and not self.service_name:
            raise ValueError("OTEL_SERVICE_NAME is required (or set OTEL_SDK_DISABLED=true)")
        return self

    def headers(self) -> dict[str, str]:
        """Заголовки OTLP-запросов: `OTEL_EXPORTER_OTLP_HEADERS` плюс Bearer из `OTEL_TOKEN`."""
        out = dict(self.otlp_headers)
        token = self.otlp_bearer_token.get_secret_value()
        if token:
            out["Authorization"] = f"Bearer {token}"
        return out


def new_config() -> Config:
    """Читает `OTEL_*`/`APP_ENV` из окружения; невалидные значения — `ConfigError`."""
    return configkit.build(Config, _KIT, ConfigError)
