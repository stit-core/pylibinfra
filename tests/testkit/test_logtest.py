from __future__ import annotations

import threading

import pytest

from pylibinfra.logkit import Logger
from pylibinfra.testkit.logtest import Capture, Entry, capture_at


def takes_logger(log: Logger) -> Logger:
    return log


def test_capture_implements_the_logger_protocol() -> None:
    recorder = Capture()

    assert isinstance(recorder, Logger)
    assert takes_logger(recorder) is recorder


def test_records_levels_and_fields() -> None:
    recorder = Capture()
    recorder.debug("debug msg", n=1)
    recorder.info("info msg", method="/svc/M")
    recorder.warn("warn msg", error="boom")
    recorder.error("error msg")

    assert [entry.level for entry in recorder.entries()] == ["debug", "info", "warn", "error"]
    assert recorder.has("info msg") is True
    assert recorder.has("info msg", level="warn") is False
    assert recorder.find("info msg").str("method") == "/svc/M"
    assert recorder.find("debug msg").str("n") == "1"
    assert recorder.find("error msg").str("absent") == ""
    assert recorder.find("never logged") is None


def test_find_returns_the_last_match_and_find_func_filters() -> None:
    recorder = Capture()
    recorder.info("grpc call", method="/svc/A", code="OK")
    recorder.info("grpc call", method="/svc/B", code="OK")

    assert recorder.find("grpc call").str("method") == "/svc/B"
    entry = recorder.find_func(lambda e: e.str("method") == "/svc/A")
    assert entry is not None
    assert entry.value("code") == "OK"
    assert entry.value("absent") is None
    assert recorder.find_func(lambda e: False) is None


def test_with_shares_the_store() -> None:
    recorder = Capture()
    child = recorder.with_(component="worker")
    child.info("started", id=7)
    child.with_(nested=True).warn("deep")

    started = recorder.find("started")
    deep = recorder.find("deep")
    assert started.value("component") == "worker"
    assert started.value("id") == 7
    assert deep.value("component") == "worker"
    assert deep.value("nested") is True


def test_capture_at_drops_below_the_min_level() -> None:
    recorder = capture_at("warn")

    assert recorder.enabled("info") is False
    assert recorder.enabled("warn") is True
    assert recorder.enabled("error") is True
    recorder.info("dropped")
    recorder.warn("kept")

    assert [entry.msg for entry in recorder.entries()] == ["kept"]


def test_capture_at_applies_to_children() -> None:
    recorder = capture_at("error")
    child = recorder.with_(k="v")
    child.warn("dropped")
    child.error("kept")

    assert [entry.msg for entry in recorder.entries()] == ["kept"]


def test_reset_clears_the_shared_store() -> None:
    recorder = Capture()
    recorder.with_(k="v").info("tick")
    recorder.reset()

    assert recorder.entries() == []


def test_entries_are_safe_under_threads() -> None:
    recorder = Capture()
    threads = [
        threading.Thread(target=lambda i=i: recorder.with_(worker=i).info("tick", i=i))  # type: ignore[misc]
        for i in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(recorder.entries()) == 8


def test_unknown_level_is_rejected() -> None:
    with pytest.raises(ValueError, match="LOG_LEVEL"):
        capture_at("verbose")


def test_entry_str_of_missing_and_non_string() -> None:
    entry = Entry(level="info", msg="m", fields={"n": 1, "none": None})

    assert entry.str("n") == "1"
    assert entry.str("none") == ""
    assert entry.value("n") == 1
