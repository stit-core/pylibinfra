"""span() — контекст-менеджер поверх tracer.start_as_current_span с
авто-записью ошибки (аналог telemetry.Span + defer(err) в golibinfra)."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Span]:
    tracer = trace.get_tracer("app")
    with tracer.start_as_current_span(name) as s:
        for k, v in attrs.items():
            s.set_attribute(k, v)
        try:
            yield s
        except Exception as exc:
            s.record_exception(exc)
            s.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
