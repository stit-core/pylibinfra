from __future__ import annotations

import io
import json
import logging
from collections.abc import Callable
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider

from pylibinfra._internal import requestid
from pylibinfra.logkit import (
    Config,
    ConfigError,
    RedactConfig,
    ServiceConfig,
    capture_stdlib,
    new,
    nop,
    register_attr,
)


def records(buf: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in buf.getvalue().splitlines()]


def last(buf: io.StringIO) -> dict[str, Any]:
    return records(buf)[-1]


def test_emits_structured_json(buf: io.StringIO) -> None:
    new(Config(), writer=buf).info("user login", user_id=42, result="ok")

    rec = last(buf)
    assert rec["msg"] == "user login"
    assert rec["level"] == "INFO"
    assert rec["user_id"] == 42
    assert rec["result"] == "ok"
    assert rec["time"].startswith("20")
    assert "source" not in rec


def test_levels_are_filtered_by_config(buf: io.StringIO) -> None:
    log = new(Config(level="warn"), writer=buf)
    log.debug("d")
    log.info("i")
    log.warn("w")
    log.error("e")

    assert [r["level"] for r in records(buf)] == ["WARN", "ERROR"]


def test_enabled_follows_level(buf: io.StringIO) -> None:
    log = new(Config(level="info"), writer=buf)

    assert log.enabled("info") is True
    assert log.enabled("ERROR") is True
    assert log.enabled("debug") is False
    with pytest.raises(ValueError, match="LOG_LEVEL"):
        log.enabled("verbose")


def test_with_binds_fields_and_keeps_chain(buf: io.StringIO) -> None:
    log = new(Config(), writer=buf)
    child = log.with_(component="worker")
    child.with_(attempt=2).info("tick", id=7)
    log.info("plain")

    bound, plain = records(buf)
    assert bound["component"] == "worker"
    assert bound["attempt"] == 2
    assert bound["id"] == 7
    assert "component" not in plain


def test_silent_discards_records(capsys: pytest.CaptureFixture[str]) -> None:
    new(Config(silent=True)).info("nowhere")

    assert capsys.readouterr().out == ""


def test_writer_beats_silent(buf: io.StringIO) -> None:
    new(Config(silent=True), writer=buf).info("kept")

    assert last(buf)["msg"] == "kept"


def test_stdout_is_the_default_sink(capsys: pytest.CaptureFixture[str]) -> None:
    new(Config()).info("to stdout", k="v")

    out = json.loads(capsys.readouterr().out.strip())
    assert out["msg"] == "to stdout"
    assert out["k"] == "v"


def test_text_format(buf: io.StringIO) -> None:
    new(Config(format="text"), writer=buf).info("hello world", k="v", n=1)

    line = buf.getvalue().strip()
    assert 'msg="hello world"' in line
    assert " k=v " in line
    assert line.endswith("n=1")
    assert line.startswith("time=")


def test_text_format_renders_structures_as_json(buf: io.StringIO) -> None:
    new(Config(format="text"), writer=buf).info("x", payload={"k": [1, 2]}, empty="")

    line = buf.getvalue().strip()
    assert 'payload={"k": [1, 2]}' in line
    assert 'empty=""' in line


def test_source_points_at_the_caller(buf: io.StringIO) -> None:
    new(Config(source=True), writer=buf).info("here")

    source = last(buf)["source"]
    assert source["file"].endswith("test_logger.py")
    assert source["function"] == "test_source_points_at_the_caller"
    assert source["line"] > 0


def test_service_fields_are_added(buf: io.StringIO) -> None:
    service = ServiceConfig(service="api", env="prod", version="v1.2.3")
    new(Config(), writer=buf, service=service).info("started")

    rec = last(buf)
    assert (rec["service"], rec["env"], rec["version"]) == ("api", "prod", "v1.2.3")


def test_empty_service_fields_are_skipped(buf: io.StringIO) -> None:
    new(Config(), writer=buf, service=ServiceConfig(service="", env="", version="")).info("x")

    assert "service" not in last(buf)


def test_request_id_comes_from_context(buf: io.StringIO) -> None:
    log = new(Config(), writer=buf)
    log.info("outside")
    token = requestid.bind("rid-1")
    try:
        log.info("inside")
    finally:
        requestid.unbind(token)

    outside, inside = records(buf)
    assert "request_id" not in outside
    assert inside["request_id"] == "rid-1"


def test_trace_ids_come_from_current_span(buf: io.StringIO) -> None:
    log = new(Config(), writer=buf)
    provider = TracerProvider()
    with provider.get_tracer("test").start_as_current_span("op"):
        log.info("traced")
    log.info("untraced")

    traced, untraced = records(buf)
    assert len(traced["trace_id"]) == 32
    assert len(traced["span_id"]) == 16
    assert "trace_id" not in untraced


def test_register_attr_deduplicates_by_key(buf: io.StringIO) -> None:
    register_attr("tenant", lambda: "first")
    register_attr("tenant", lambda: "second")
    register_attr("absent", lambda: None)
    new(Config(), writer=buf).info("x")

    rec = last(buf)
    assert rec["tenant"] == "second"
    assert "absent" not in rec
    assert list(rec).count("tenant") == 1


def test_register_attr_rejects_empty_key_and_non_callable() -> None:
    with pytest.raises(ValueError, match="empty key"):
        register_attr("", lambda: "x")
    with pytest.raises(ValueError, match="not callable"):
        register_attr("k", "nope")  # type: ignore[arg-type]


def test_register_attr_replaces_only_its_own_key(buf: io.StringIO) -> None:
    register_attr("tenant", lambda: "acme")
    register_attr("zone", lambda: "eu")
    register_attr("tenant", lambda: "globex")
    new(Config(), writer=buf).info("x")

    rec = last(buf)
    assert rec["tenant"] == "globex"
    assert rec["zone"] == "eu"


def test_failing_ctx_attr_does_not_break_the_record(buf: io.StringIO) -> None:
    def boom() -> str:
        raise RuntimeError("extractor down")

    register_attr("boom", boom)
    register_attr("zone", lambda: "eu")
    new(Config(), writer=buf).info("x")

    rec = last(buf)
    assert "boom" not in rec
    assert rec["zone"] == "eu"


@pytest.mark.parametrize("key", ["time", "level", "msg", "trace_id", "span_id"])
def test_record_keys_are_not_overwritten_by_fields(buf: io.StringIO, key: str) -> None:
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("op"):
        new(Config(), writer=buf).info("real message", **{key: "hijacked"})

    rec = last(buf)
    assert rec["level"] == "INFO"
    assert rec["msg"] == "real message"
    assert rec[key] != "hijacked"


def test_source_key_is_reserved_only_when_enabled(buf: io.StringIO) -> None:
    new(Config(), writer=buf).info("x", source="my-field")
    assert last(buf)["source"] == "my-field"

    new(Config(source=True), writer=buf).info("y", source="my-field")
    assert last(buf)["source"]["function"].endswith("test_source_key_is_reserved_only_when_enabled")


def test_metric_counts_emitted_records_only(
    buf: io.StringIO, read_metric: Callable[..., float]
) -> None:
    log = new(Config(level="info"), writer=buf)
    log.info("one")
    log.error("two")
    log.debug("filtered out")

    assert read_metric("log_messages_total", {"level": "info"}) == 1
    assert read_metric("log_messages_total", {"level": "error"}) == 1
    assert read_metric("log_messages_total", {"level": "debug"}) == 0


def test_loggers_are_independent(buf: io.StringIO) -> None:
    other = io.StringIO()
    new(Config(), writer=buf).info("first")
    new(Config(level="error"), writer=other).info("dropped")

    assert len(records(buf)) == 1
    assert other.getvalue() == ""


def test_invalid_config_fails_at_construction(buf: io.StringIO) -> None:
    broken = Config.model_construct(level="nope", format="json", source=False, silent=True)

    with pytest.raises(ConfigError, match="LOG_LEVEL"):
        new(broken, writer=buf)


def test_invalid_redact_config_fails_at_construction(buf: io.StringIO) -> None:
    broken = RedactConfig.model_construct(enabled=True, patterns=("(",), replace_with="[X]")

    with pytest.raises(ConfigError, match="LOG_REDACT_PATTERNS"):
        new(Config(), writer=buf, redact=broken)


def test_capture_stdlib_does_not_raise_into_third_party_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Broken(io.StringIO):
        def write(self, text: str, /) -> int:
            raise OSError("sink is gone")

    monkeypatch.setattr(logging, "raiseExceptions", False)
    capture_stdlib(new(Config(), writer=Broken()), Config())

    logging.getLogger("uvicorn").info("request served")


def test_nop_writes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    log = nop()
    log.info("x")
    log.with_(k="v").error("y")

    assert log.enabled("error") is False
    assert capsys.readouterr().out == ""


def test_capture_stdlib_routes_third_party_records(buf: io.StringIO) -> None:
    capture_stdlib(new(Config(), writer=buf), Config())
    logging.getLogger("uvicorn.error").warning("started on %s", 8080)

    rec = last(buf)
    assert rec["msg"] == "started on 8080"
    assert rec["level"] == "WARN"
    assert rec["logger"] == "uvicorn.error"


def test_capture_stdlib_maps_all_levels(buf: io.StringIO) -> None:
    capture_stdlib(new(Config(level="debug"), writer=buf), Config(level="debug"))
    third_party = logging.getLogger("grpc")
    third_party.debug("d")
    third_party.info("i")
    third_party.warning("w")
    third_party.error("e")

    assert [rec["level"] for rec in records(buf)] == ["DEBUG", "INFO", "WARN", "ERROR"]


def test_capture_stdlib_keeps_level_and_exception(buf: io.StringIO) -> None:
    capture_stdlib(new(Config(level="error"), writer=buf), Config(level="error"))
    third_party = logging.getLogger("aiogram")
    third_party.info("dropped by level")
    try:
        raise ValueError("boom")
    except ValueError:
        third_party.exception("handler failed")

    rec = last(buf)
    assert len(records(buf)) == 1
    assert rec["msg"] == "handler failed"
    assert "ValueError: boom" in rec["exception"]
