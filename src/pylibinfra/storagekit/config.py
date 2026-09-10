"""Config кита: адрес S3, креды, адресация бакета, multipart, ретраи, стартовые проверки."""

from __future__ import annotations

from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator

from pylibinfra._internal import configkit
from pylibinfra._internal.configkit import KitSettings

from .errors import ConfigError

_KIT = "storagekit"

Credentials = Literal["static", "chain"]
BucketLookup = Literal["auto", "path", "dns"]
URLStyle = Literal["path", "virtual"]

CREDENTIALS_STATIC: Credentials = "static"
CREDENTIALS_CHAIN: Credentials = "chain"

BUCKET_LOOKUP_AUTO: BucketLookup = "auto"
BUCKET_LOOKUP_PATH: BucketLookup = "path"
BUCKET_LOOKUP_DNS: BucketLookup = "dns"

URL_STYLE_PATH: URLStyle = "path"
URL_STYLE_VIRTUAL: URLStyle = "virtual"

MIN_PART_SIZE = 5 << 20
MAX_PART_SIZE = 5 << 30
DEFAULT_PART_SIZE = 16 << 20

# Регион нужен подписи SigV4; у MinIO он произволен, поэтому пустое значение
# конфига подписывается общепринятым us-east-1, а не отвергается.
DEFAULT_REGION = "us-east-1"


class Config(KitSettings):
    """Параметры S3-адаптера; имена полей и env-alias'ы — public API кита.

    `MemoryStorage` конфига не требует.
    """

    model_config = configkit.settings_config()

    endpoint: str = Field(default="", validation_alias="STORAGE_S3_ENDPOINT")
    access_key: SecretStr = Field(default=SecretStr(""), validation_alias="STORAGE_S3_ACCESS_KEY")
    secret_key: SecretStr = Field(default=SecretStr(""), validation_alias="STORAGE_S3_SECRET_KEY")
    credentials: Credentials = Field(
        default=CREDENTIALS_STATIC, validation_alias="STORAGE_S3_CREDENTIALS"
    )
    bucket: str = Field(default="", validation_alias="STORAGE_S3_BUCKET")
    region: str = Field(default="", validation_alias="STORAGE_S3_REGION")
    use_ssl: bool = Field(default=False, validation_alias="STORAGE_S3_USE_SSL")
    bucket_lookup: BucketLookup = Field(
        default=BUCKET_LOOKUP_AUTO, validation_alias="STORAGE_S3_BUCKET_LOOKUP"
    )
    create_bucket: bool = Field(default=False, validation_alias="STORAGE_S3_CREATE_BUCKET")
    part_size: int = Field(default=DEFAULT_PART_SIZE, validation_alias="STORAGE_S3_PART_SIZE")
    max_retries: int = Field(default=3, validation_alias="STORAGE_S3_MAX_RETRIES")
    probe_timeout: float = Field(default=5.0, validation_alias="STORAGE_S3_PROBE_TIMEOUT")
    probe_on_start: bool = Field(default=True, validation_alias="STORAGE_S3_PROBE_ON_START")
    url_style: URLStyle = Field(default=URL_STYLE_PATH, validation_alias="STORAGE_S3_URL_STYLE")
    public_base_url: str = Field(default="", validation_alias="STORAGE_PUBLIC_BASE_URL")

    @model_validator(mode="after")
    def _check(self) -> Self:
        """Проверяет инварианты; текст ошибки называет ENV-переменную, а не поле."""
        _parse_endpoint(self.endpoint, use_ssl=self.use_ssl)
        if not self.bucket.strip():
            raise ValueError("STORAGE_S3_BUCKET is required")
        if any(ch in self.bucket for ch in "/\\ "):
            raise ValueError(f"STORAGE_S3_BUCKET {self.bucket!r} must be a bucket name, not a path")
        if self.credentials == CREDENTIALS_STATIC and bool(
            self.access_key.get_secret_value()
        ) != bool(self.secret_key.get_secret_value()):
            raise ValueError("STORAGE_S3_ACCESS_KEY and STORAGE_S3_SECRET_KEY must be set together")
        if not MIN_PART_SIZE <= self.part_size <= MAX_PART_SIZE:
            raise ValueError(
                f"STORAGE_S3_PART_SIZE {self.part_size} outside "
                f"[{MIN_PART_SIZE}, {MAX_PART_SIZE}] bytes"
            )
        if self.max_retries < 0:
            raise ValueError(f"STORAGE_S3_MAX_RETRIES must be >= 0, got {self.max_retries}")
        if self.probe_timeout < 0:
            raise ValueError(f"STORAGE_S3_PROBE_TIMEOUT must be >= 0, got {self.probe_timeout:g}")
        if self.public_base_url:
            parts = urlsplit(self.public_base_url)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ValueError(
                    f"STORAGE_PUBLIC_BASE_URL {self.public_base_url!r} "
                    "must be an absolute http(s) URL"
                )
        return self

    def endpoint_url(self) -> str:
        """Адрес S3 для клиента `scheme://host[:port]`; схему задаёт endpoint либо `use_ssl`."""
        host, secure = _parse_endpoint(self.endpoint, use_ssl=self.use_ssl)
        return f"{'https' if secure else 'http'}://{host}"

    def signing_region(self) -> str:
        """Регион для подписи SigV4: `STORAGE_S3_REGION`, а пустой — `DEFAULT_REGION`."""
        return self.region or DEFAULT_REGION


def _parse_endpoint(raw: str, *, use_ssl: bool) -> tuple[str, bool]:
    """Разбирает `STORAGE_S3_ENDPOINT` в `host[:port]` и флаг TLS; невалидный — `ValueError`."""
    value = raw.strip()
    if not value:
        raise ValueError("STORAGE_S3_ENDPOINT is required")
    if "://" not in value:
        _check_host_port(value)
        return value, use_ssl

    parts = urlsplit(value)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"STORAGE_S3_ENDPOINT {raw!r}: scheme must be http or https")
    if not parts.netloc or parts.path.strip("/") or parts.query or "@" in parts.netloc:
        raise ValueError(
            f"STORAGE_S3_ENDPOINT {raw!r}: expected scheme://host[:port] "
            "without path, query or credentials"
        )
    _check_host_port(parts.netloc)
    secure = parts.scheme == "https"
    if use_ssl and not secure:
        raise ValueError(f"STORAGE_S3_ENDPOINT {raw!r} is http, but STORAGE_S3_USE_SSL is true")
    return parts.netloc, secure


def _check_host_port(host_port: str) -> None:
    """Проверяет `host[:port]`: непустой хост, числовой порт, без пути, query и кредов."""
    if any(ch in host_port for ch in "/?#@ "):
        raise ValueError(
            f"STORAGE_S3_ENDPOINT {host_port!r}: expected host[:port] "
            "without path, query or credentials"
        )
    try:
        parts = urlsplit(f"//{host_port}")
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"STORAGE_S3_ENDPOINT {host_port!r}: {exc}") from None
    if not parts.hostname:
        raise ValueError(f"STORAGE_S3_ENDPOINT {host_port!r}: empty host")
    if host_port.endswith(":") or (port is None and host_port.rpartition("]")[2].count(":")):
        raise ValueError(f"STORAGE_S3_ENDPOINT {host_port!r}: empty port")


def new_config(**values: object) -> Config:
    """Читает `STORAGE_S3_*`/`STORAGE_PUBLIC_BASE_URL` из окружения; невалидное — `ConfigError`.

    `values` перекрывают окружение — так конфиг собирают тесты и код, у которого
    адрес хранилища приходит не из ENV.
    """
    return configkit.build(Config, _KIT, ConfigError, **values)
