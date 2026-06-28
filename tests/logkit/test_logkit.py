import json
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider

from pylibinfra.logkit import Config, RedactConfig, configure, new_logger


def _last_record(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    lines = capsys.readouterr().out.strip().splitlines()
    record: dict[str, Any] = json.loads(lines[-1])
    return record


def test_emits_structured_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure(Config(), redact_cfg=RedactConfig(), use_otel=False)
    new_logger().info("user login", user_id=42, result="ok")
    rec = _last_record(capsys)
    assert rec["message"] == "user login"
    assert rec["level"] == "info"
    assert rec["user_id"] == 42
    assert rec["result"] == "ok"


def test_with_binds_fields(capsys: pytest.CaptureFixture[str]) -> None:
    configure(Config(), redact_cfg=RedactConfig(), use_otel=False)
    new_logger().with_(request_id="abc").info("hi")
    rec = _last_record(capsys)
    assert rec["request_id"] == "abc"


def test_redacts_pii(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_REDACT_ENABLED", "true")
    configure(Config(), redact_cfg=RedactConfig(), use_otel=False)
    new_logger().info("contact", email="john@example.com")
    rec = _last_record(capsys)
    assert "[REDACTED]" in rec["email"]
    assert "john@example.com" not in rec["email"]


def test_redacts_nested_dict(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOG_REDACT_ENABLED", "true")
    configure(Config(), redact_cfg=RedactConfig(), use_otel=False)
    new_logger().info("contact", payload={"inner": {"email": "john@example.com"}})
    rec = _last_record(capsys)
    assert rec["payload"]["inner"]["email"] == "[REDACTED]"


def test_redacts_inside_list(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOG_REDACT_ENABLED", "true")
    configure(Config(), redact_cfg=RedactConfig(), use_otel=False)
    new_logger().info("contacts", emails=["a@example.com", "plain"])
    rec = _last_record(capsys)
    assert rec["emails"] == ["[REDACTED]", "plain"]


def test_redacts_card_like_int(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOG_REDACT_ENABLED", "true")
    configure(Config(), redact_cfg=RedactConfig(), use_otel=False)
    new_logger().info("pay", card=4111111111111111, tg_id=42)
    rec = _last_record(capsys)
    # длинный int, похожий на карту, — затёрт строкой
    assert rec["card"] == "[REDACTED]"
    # короткий int не тронут и остался int
    assert rec["tg_id"] == 42
    assert isinstance(rec["tg_id"], int)


def test_injects_trace_context(capsys: pytest.CaptureFixture[str]) -> None:
    configure(Config(), redact_cfg=RedactConfig(), use_otel=False)
    provider = TracerProvider()
    with provider.get_tracer("test").start_as_current_span("op"):
        new_logger().info("traced")
    rec = _last_record(capsys)
    assert len(rec["trace_id"]) == 32
    assert len(rec["span_id"]) == 16


def test_stdout_disabled_no_output(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOG_STDOUT", "false")
    configure(Config(), redact_cfg=RedactConfig(), use_otel=False)
    new_logger().info("should not reach stdout")
    assert capsys.readouterr().out.strip() == ""
