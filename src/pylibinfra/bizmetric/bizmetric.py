"""bizmetric — конвенциональный helper для бизнес-метрик.

Все имена форсятся в форму ``biz_<domain>_<name>_<suffix>``:
  - counter   → суффикс ``_total``
  - histogram → суффикс ``_seconds``
  - gauge     → без суффикса

Resource-attrs (service.name, tenant, environment) добавляются OTel SDK
автоматически — повторно их в labels класть не нужно.

Префикс ``biz_`` нужен, чтобы дашборд "Apps/business" использовал автодискавер
``{__name__=~"biz_.*"}`` без ручного списка метрик. Невалидные domain/name
(не snake_case ASCII) → ValueError (аналог panic в golibinfra).
"""

import re

from opentelemetry.metrics import Counter, Histogram, Meter, UpDownCounter

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def counter(meter: Meter, domain: str, name: str, *, description: str = "") -> Counter:
    full = must_name(domain, name) + "_total"
    return meter.create_counter(full, description=description)


def histogram(meter: Meter, domain: str, name: str, *, description: str = "") -> Histogram:
    full = must_name(domain, name) + "_seconds"
    return meter.create_histogram(full, unit="s", description=description)


def gauge(meter: Meter, domain: str, name: str, *, description: str = "") -> UpDownCounter:
    """Int64UpDownCounter как способ выразить gauge для non-observable значений.

    Для observable-источников используйте meter.create_observable_gauge напрямую
    и валидируйте имя через must_name.
    """
    return meter.create_up_down_counter(must_name(domain, name), description=description)


def must_name(domain: str, name: str) -> str:
    if not _NAME_RE.match(domain):
        raise ValueError(f"bizmetric: invalid domain {domain!r} (need ^[a-z][a-z0-9_]*$)")
    if not _NAME_RE.match(name):
        raise ValueError(f"bizmetric: invalid name {name!r} (need ^[a-z][a-z0-9_]*$)")
    return f"biz_{domain}_{name}"
