"""Фабрики бизнес-метрик и валидатор имени `biz_<domain>_<name>`."""

from __future__ import annotations

import re

from opentelemetry.metrics import Counter, Histogram, Meter, UpDownCounter

# Публичного алиаса у sync-gauge в otel-python нет — тип экспортируется как _Gauge.
from opentelemetry.metrics import _Gauge as Gauge

from pylibinfra._internal import otelkit

UNIT_EVENT = "{event}"
UNIT_ITEM = "{item}"
UNIT_SECONDS = "s"

# \Z, а не $: `$` в Python совпадает и перед завершающим \n, и имя вроде "auth\n"
# прошло бы валидацию, сломав экспозицию метрики.
_NAME_RE = re.compile(r"\A[a-z][a-z0-9_]*\Z")
_PREFIX = "biz_"
# `_total`/`_seconds` подставляет сам helper, `_count`/`_sum`/`_bucket`/`_created` —
# Prometheus-трансляция гистограмм и счётчиков: в domain/name они дают двойной
# суффикс и коллизию с чужой серией.
_RESERVED_SUFFIXES = ("_total", "_seconds", "_count", "_sum", "_bucket", "_created")


class InvalidNameError(ValueError):
    """`domain`/`name` вне конвенции: не snake_case ASCII, префикс `biz_` или занятый суффикс."""


def counter(
    meter: Meter, domain: str, name: str, *, description: str = "", unit: str = UNIT_EVENT
) -> Counter:
    """Монотонный счётчик событий `biz_<domain>_<name>_total`; невалидное имя — InvalidNameError."""
    return meter.create_counter(
        must_name(domain, name) + "_total", unit=unit, description=description
    )


def histogram(meter: Meter, domain: str, name: str, *, description: str = "") -> Histogram:
    """Гистограмма длительности `biz_<domain>_<name>_seconds` с общими бакетами репозитория."""
    return otelkit.duration_histogram(meter, must_name(domain, name) + "_seconds", description)


def gauge(
    meter: Meter, domain: str, name: str, *, description: str = "", unit: str = UNIT_ITEM
) -> Gauge:
    """Gauge `biz_<domain>_<name>`: `set` пишет текущее значение величины."""
    return meter.create_gauge(must_name(domain, name), unit=unit, description=description)


def up_down_counter(
    meter: Meter, domain: str, name: str, *, description: str = "", unit: str = UNIT_ITEM
) -> UpDownCounter:
    """Счётчик с дельтами `biz_<domain>_<name>`: `add(+1)`/`add(-1)` вокруг текущего значения."""
    return meter.create_up_down_counter(must_name(domain, name), unit=unit, description=description)


def must_name(domain: str, name: str) -> str:
    """Собирает и валидирует имя `biz_<domain>_<name>` — для инструментов, заводимых вручную."""
    _validate("domain", domain)
    _validate("name", name)
    return f"{_PREFIX}{domain}_{name}"


def _validate(what: str, value: str) -> None:
    if not _NAME_RE.match(value):
        raise InvalidNameError(f"bizmetric: invalid {what} {value!r} (need ^[a-z][a-z0-9_]*$)")
    if value.startswith(_PREFIX):
        raise InvalidNameError(
            f"bizmetric: invalid {what} {value!r}: prefix {_PREFIX} is added by the helper"
        )
    for suffix in _RESERVED_SUFFIXES:
        if value.endswith(suffix):
            raise InvalidNameError(
                f"bizmetric: invalid {what} {value!r}: suffix {suffix} is reserved "
                f"(added by the helper or by Prometheus translation)"
            )
