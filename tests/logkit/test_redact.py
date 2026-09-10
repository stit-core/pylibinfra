from __future__ import annotations

import io
import json
from typing import Any

import pytest

from pylibinfra.logkit import Config, RedactConfig, new
from pylibinfra.logkit.redact import compile_rules, default_hint


def enabled(**kwargs: Any) -> RedactConfig:
    return RedactConfig(enabled=True, **kwargs)


def last(buf: io.StringIO) -> dict[str, Any]:
    record: dict[str, Any] = json.loads(buf.getvalue().splitlines()[-1])
    return record


@pytest.mark.parametrize(
    "secret",
    [
        "john@example.com",
        "+79161234567",
        "89161234567",
        "4111 1111 1111 1111",
        "Bearer abc.def-ghi",
        "Basic QWxhZGRpbjpvcGVu",
        "eyJhbGciOi.eyJzdWIiOi.sig",
        "sk-ant-api03-AAAAAAAAAAAAAAAA",
        "sk-abcdefghijklmnopqrstuvwx",
    ],
)
def test_default_set_masks_pii_and_secrets(buf: io.StringIO, secret: str) -> None:
    new(Config(), writer=buf, redact=enabled()).info("payload", value=secret)

    assert last(buf)["value"] == "[REDACTED]"


def test_message_is_masked(buf: io.StringIO) -> None:
    new(Config(), writer=buf, redact=enabled()).info("login john@example.com done")

    assert last(buf)["msg"] == "login [REDACTED] done"


def test_numbers_bool_and_none_are_untouched(buf: io.StringIO) -> None:
    new(Config(), writer=buf, redact=enabled()).info(
        "pay", card=4111111111111111, tg_id=42, ok=True, missing=None, elapsed=1.5
    )

    rec = last(buf)
    assert rec["card"] == 4111111111111111
    assert rec["tg_id"] == 42
    assert rec["ok"] is True
    assert rec["missing"] is None
    assert rec["elapsed"] == 1.5


def test_nested_structures_are_masked(buf: io.StringIO) -> None:
    new(Config(), writer=buf, redact=enabled()).info(
        "contact",
        payload={"inner": {"email": "john@example.com"}},
        emails=["a@example.com", "plain"],
        pair=("b@example.com", 1),
    )

    rec = last(buf)
    assert rec["payload"]["inner"]["email"] == "[REDACTED]"
    assert rec["emails"] == ["[REDACTED]", "plain"]
    assert rec["pair"] == ["[REDACTED]", 1]


def test_with_fields_are_masked(buf: io.StringIO) -> None:
    log = new(Config(), writer=buf, redact=enabled())
    log.with_(owner="owner@example.com").info("bound")

    assert last(buf)["owner"] == "[REDACTED]"


def test_exception_text_is_masked(buf: io.StringIO) -> None:
    new(Config(), writer=buf, redact=enabled()).error(
        "failed", error=ValueError("no user john@example.com")
    )

    assert last(buf)["error"] == "no user [REDACTED]"


def test_custom_set_replaces_the_default(buf: io.StringIO) -> None:
    redact = enabled(patterns=(r"secret-\d+",))
    new(Config(), writer=buf, redact=redact).info("x", custom="secret-42", email="john@example.com")

    rec = last(buf)
    assert rec["custom"] == "[REDACTED]"
    assert rec["email"] == "john@example.com"


def test_custom_replacement(buf: io.StringIO) -> None:
    new(Config(), writer=buf, redact=enabled(replace_with="***")).info(
        "x", email="john@example.com"
    )

    assert last(buf)["email"] == "***"


def test_disabled_by_default(buf: io.StringIO) -> None:
    new(Config(), writer=buf, redact=RedactConfig()).info("x", email="john@example.com")

    assert last(buf)["email"] == "john@example.com"


def test_service_fields_bypass_redact(buf: io.StringIO) -> None:
    from pylibinfra.logkit import ServiceConfig

    new(
        Config(),
        writer=buf,
        redact=enabled(patterns=("api",)),
        service=ServiceConfig(service="api"),
    ).info("x", note="api")

    rec = last(buf)
    assert rec["service"] == "api"
    assert rec["note"] == "[REDACTED]"


@pytest.mark.parametrize(
    ("value", "hit"),
    [
        ("john@example.com", True),
        ("+79161234567", True),
        ("4111 1111 1111 1111", True),
        ("BEARER abc", True),
        ("basic abc", True),
        ("eyJhbGciOi", True),
        ("sk-ant-x", True),
        ("order 12345 shipped", False),
        ("a1b2c3d4e5f6", False),
        ("", False),
    ],
)
def test_pre_filter_skips_records_without_markers(value: str, hit: bool) -> None:
    assert default_hint(value) is hit


def test_default_set_and_pre_filter_agree_on_ascii_only() -> None:
    # Юникод-пробел рвёт цепочку цифр и в pre-filter'е, и в маске: несовпадение
    # означало бы «regex совпал, а pre-filter не пустил» — тихую утечку PII.
    nbsp_card = "1234\xa05678\xa09012\xa03456"
    redactor = compile_rules((), "[REDACTED]")

    assert default_hint(nbsp_card) is False
    assert redactor.text(nbsp_card) == nbsp_card


@pytest.mark.parametrize(
    "value",
    [
        "john@example.com",
        "+79161234567",
        "4111 1111 1111 1111",
        "Authorization: Bearer abc.def",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig",
        "sk-ant-abcdefghijklmnopqrst",
        "order 12345 shipped",
        "1234\xa05678\xa09012\xa03456",
        "١٢٣٤٥٦٧٨٩٠١",
    ],
)
def test_pre_filter_never_hides_a_match(value: str) -> None:
    redactor = compile_rules((), "[REDACTED]")

    if redactor.text(value) != value:
        assert default_hint(value) is True


def test_pre_filter_is_default_set_only(buf: io.StringIO) -> None:
    new(Config(), writer=buf, redact=enabled(patterns=("plainword",))).info("x", k="plainword")

    assert last(buf)["k"] == "[REDACTED]"


def test_arbitrary_object_is_masked_by_its_text(buf: io.StringIO) -> None:
    class Payload:
        def __repr__(self) -> str:
            return "Payload(email=john@example.com)"

    new(Config(), writer=buf, redact=enabled()).info("x", obj=Payload(), other=object())

    assert last(buf)["obj"] == "Payload(email=[REDACTED])"
