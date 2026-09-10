from __future__ import annotations

import io
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from pylibinfra import logkit
from pylibinfra.clockkit import Tickable
from pylibinfra.telemetry.errorlog import (
    ERROR_LOG_WINDOW,
    OTEL_LOGGER,
    _Limiter,
    install_error_logging,
)


def records(buf: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in buf.getvalue().splitlines()]


@pytest.fixture
def sink() -> io.StringIO:
    buf = io.StringIO()
    install_error_logging(logkit.new(logkit.Config(level="debug"), writer=buf))
    return buf


@pytest.mark.parametrize(
    ("emit", "level"),
    [
        ("error", "ERROR"),
        ("warning", "WARN"),
        ("info", "DEBUG"),
        ("debug", "DEBUG"),
    ],
)
def test_sdk_levels_are_mapped(sink: io.StringIO, emit: str, level: str) -> None:
    getattr(logging.getLogger("opentelemetry.sdk.trace"), emit)("sdk says %s", "hi")

    record = records(sink)[0]
    assert record["level"] == level
    assert record["msg"] == "sdk says hi"
    assert record["logger"] == "opentelemetry.sdk.trace"


def test_exception_text_is_attached(sink: io.StringIO) -> None:
    try:
        raise ConnectionError("collector down")
    except ConnectionError:
        logging.getLogger("opentelemetry.exporter").exception("export failed")

    assert "ConnectionError: collector down" in records(sink)[0]["exception"]


def test_broken_sink_does_not_raise_into_the_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken(io.StringIO):
        def write(self, text: str, /) -> int:
            raise OSError("sink is gone")

    monkeypatch.setattr(logging, "raiseExceptions", False)
    install_error_logging(logkit.new(logkit.Config(), writer=Broken()))

    logging.getLogger("opentelemetry.exporter").error("collector unreachable")


def test_records_do_not_propagate_to_root(sink: io.StringIO) -> None:
    assert logging.getLogger(OTEL_LOGGER).propagate is False


def test_repeated_text_is_throttled(sink: io.StringIO) -> None:
    log = logging.getLogger("opentelemetry.exporter")
    for _ in range(5):
        log.error("collector unreachable")
    log.error("another problem")

    assert [record["msg"] for record in records(sink)] == [
        "collector unreachable",
        "another problem",
    ]


def test_installing_twice_does_not_duplicate_records() -> None:
    buf = io.StringIO()
    log = logkit.new(logkit.Config(), writer=buf)
    install_error_logging(log)
    install_error_logging(log)

    logging.getLogger("opentelemetry.sdk").error("once")

    assert len(records(buf)) == 1


def test_limiter_allows_after_the_window() -> None:
    clock = Tickable(datetime(2026, 1, 1, tzinfo=UTC))
    limiter = _Limiter(ERROR_LOG_WINDOW, clock)

    assert limiter.allow("boom") is True
    assert limiter.allow("boom") is False
    clock.advance(timedelta(seconds=ERROR_LOG_WINDOW))
    assert limiter.allow("boom") is True


def test_limiter_caps_tracked_keys() -> None:
    clock = Tickable(datetime(2026, 1, 1, tzinfo=UTC))
    limiter = _Limiter(1.0, clock)
    for i in range(300):
        limiter.allow(f"key-{i}")

    assert len(limiter._seen) <= 256
    clock.advance(timedelta(seconds=2))
    assert limiter.allow("key-299") is True


def test_limiter_drops_stale_keys_at_the_cap() -> None:
    clock = Tickable(datetime(2026, 1, 1, tzinfo=UTC))
    limiter = _Limiter(1.0, clock)
    for i in range(256):
        limiter.allow(f"key-{i}")
    clock.advance(timedelta(seconds=2))

    limiter.allow("fresh")

    assert limiter._seen == {"fresh": clock.now()}
