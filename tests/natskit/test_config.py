"""Env-контракт natskit: дефолты, префиксы инстансов, валидация и редактирование URL."""

from __future__ import annotations

import pytest

from pylibinfra.natskit import (
    DEFAULT_INSTANCE,
    BackpressureConfig,
    Config,
    ConfigError,
    JetStreamConfig,
    config_without_env,
    env_prefix,
    new_config,
    new_config_prefixed,
    redact_url,
    subject_matches,
)
from pylibinfra.natskit.config import positive_int_or, positive_or
from pylibinfra.natskit.jetstream import parse_retention
from pylibinfra.natskit.subjects import (
    valid_instance_name,
    valid_publish_subject,
    valid_stream_name,
)

_ENV = [
    "NATS_URL",
    "NATS_CONNECT_TIMEOUT",
    "NATS_RECONNECT_WAIT",
    "NATS_MAX_RECONNECTS",
    "NATS_DRAIN_TIMEOUT",
    "NATS_HANDLER_TIMEOUT",
    "NATS_CREDS_FILE",
    "NATS_NKEY_SEED_FILE",
    "NATS_TLS_CA_FILE",
    "NATS_MAX_IN_FLIGHT",
    "NATS_JETSTREAM_ENABLED",
    "NATS_JS_STREAM_NAMES",
    "NATS_JS_STREAM_MANAGED",
    "NATS_JS_RETENTION",
    "NATS_JS_MAX_AGE",
    "NATS_JS_DUPLICATES",
    "NATS_JS_MAX_IN_FLIGHT",
    "NATS_JS_ACK_WAIT",
    "NATS_JS_HANDLER_TIMEOUT",
    "NATS_JS_PUBLISH_ASYNC_TIMEOUT",
    "NATS_JS_MAX_DELIVER",
    "NATS_JS_NAK_BACKOFF",
    "NATS_JS_DLQ_SUBJECT",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"HUB_{name}", raising=False)


def test_defaults_are_safe() -> None:
    cfg = new_config()

    assert cfg.url == "nats://localhost:4222"
    assert cfg.connect_timeout == 5.0
    assert cfg.reconnect_wait == 2.0
    # -1: NATS-maintenance любой длины переживается без потери шины.
    assert cfg.max_reconnects == -1
    assert cfg.drain_timeout == 10.0
    assert cfg.handler_timeout == 30.0
    assert cfg.backpressure.max_in_flight == 100
    assert cfg.jetstream.enabled is False
    assert cfg.jetstream.stream_managed is True
    assert cfg.jetstream.retention == "limits"
    assert cfg.jetstream.max_age == 168 * 3600
    assert cfg.jetstream.duplicates == 120.0
    assert cfg.jetstream.ack_wait == 30.0
    assert cfg.jetstream.handler_timeout == 0.0
    assert cfg.jetstream.publish_async_timeout == 5.0
    assert cfg.jetstream.max_deliver == 10
    assert cfg.jetstream.nak_backoff == 1.0
    assert cfg.jetstream.dlq_subject == ""


def test_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NATS_URL", "tls://nats.example:4222")
    monkeypatch.setenv("NATS_MAX_RECONNECTS", "7")
    monkeypatch.setenv("NATS_MAX_IN_FLIGHT", "8")
    monkeypatch.setenv("NATS_JETSTREAM_ENABLED", "true")
    monkeypatch.setenv("NATS_JS_STREAM_NAMES", " ORDERS , LEADS ,")
    monkeypatch.setenv("NATS_JS_RETENTION", "WorkQueue")
    monkeypatch.setenv("NATS_JS_DLQ_SUBJECT", "dlq.orders")

    cfg = new_config()

    assert cfg.url == "tls://nats.example:4222"
    assert cfg.max_reconnects == 7
    assert cfg.backpressure.max_in_flight == 8
    assert cfg.jetstream.enabled is True
    assert cfg.jetstream.stream_names == ("ORDERS", "LEADS")
    assert cfg.jetstream.retention == "workqueue"
    assert cfg.jetstream.dlq_subject == "dlq.orders"


def test_empty_env_value_means_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # `NATS_MAX_RECONNECTS=` в .env и Helm с value: "" не должны обнулять дефолт.
    monkeypatch.setenv("NATS_MAX_RECONNECTS", "")
    monkeypatch.setenv("NATS_JS_NAK_BACKOFF", "")

    cfg = new_config()

    assert cfg.max_reconnects == -1
    assert cfg.jetstream.nak_backoff == 1.0


def test_prefixed_config_is_isolated_from_bare_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NATS_URL", "nats://own:4222")
    monkeypatch.setenv("NATS_JS_STREAM_NAMES", "OWN")
    monkeypatch.setenv("HUB_NATS_URL", "nats://hub:4222")
    monkeypatch.setenv("HUB_NATS_MAX_IN_FLIGHT", "5")

    cfg = new_config_prefixed("hub")

    assert cfg.url == "nats://hub:4222"
    assert cfg.backpressure.max_in_flight == 5
    # Незаданная префиксная переменная берёт дефолт кита, а не голую NATS_*.
    assert cfg.jetstream.stream_names == ()


def test_prefixed_config_rejects_invalid_instance_name() -> None:
    with pytest.raises(ConfigError, match="instance"):
        new_config_prefixed("hub/v2")
    with pytest.raises(ConfigError, match="instance"):
        new_config_prefixed("")


def test_prefixed_config_reports_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_NATS_JS_ACK_WAIT", "0.5")

    with pytest.raises(ConfigError, match="NATS_JS_ACK_WAIT"):
        new_config_prefixed("hub")


@pytest.mark.parametrize(
    ("env", "value", "message"),
    [
        ("NATS_URL", "  ", "NATS_URL is empty"),
        ("NATS_URL", "nats://a:1,,nats://b:2", "empty entry"),
        ("NATS_URL", "amqp://host:5672", "unsupported scheme"),
        ("NATS_URL", "nats://", "host is empty"),
        ("NATS_MAX_RECONNECTS", "-2", "NATS_MAX_RECONNECTS"),
        ("NATS_CONNECT_TIMEOUT", "-1", "NATS_CONNECT_TIMEOUT"),
        ("NATS_CREDS_FILE", "/nope/creds.creds", "NATS_CREDS_FILE"),
        ("NATS_JS_ACK_WAIT", "0.5", "NATS_JS_ACK_WAIT"),
        ("NATS_JS_RETENTION", "forever", "NATS_JS_RETENTION"),
        ("NATS_JS_STREAM_NAMES", "ORDERS.*", "NATS_JS_STREAM_NAMES"),
        ("NATS_JS_DLQ_SUBJECT", "dlq..orders", "NATS_JS_DLQ_SUBJECT"),
        ("NATS_JS_DLQ_SUBJECT", "dlq.>", "NATS_JS_DLQ_SUBJECT"),
        ("NATS_JS_MAX_DELIVER", "-1", "NATS_JS_MAX_DELIVER"),
    ],
)
def test_invalid_values_fail_fast(
    monkeypatch: pytest.MonkeyPatch, env: str, value: str, message: str
) -> None:
    monkeypatch.setenv(env, value)

    with pytest.raises(ConfigError, match=message):
        new_config()


def test_auth_files_must_exist(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    creds = f"{tmp_path}/user.creds"
    with open(creds, "w") as fh:
        fh.write("-----BEGIN NATS USER JWT-----\n")
    monkeypatch.setenv("NATS_CREDS_FILE", creds)

    assert new_config().creds_file == creds


def test_config_error_hides_userinfo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NATS_URL", "amqp://user:s3cret@host:5672")

    with pytest.raises(ConfigError) as excinfo:
        new_config()

    assert "s3cret" not in str(excinfo.value)
    assert "xxxxx" in str(excinfo.value)


def test_redact_url_hides_userinfo() -> None:
    assert redact_url("nats://user:pass@host:4222") == "nats://xxxxx@host:4222"
    assert redact_url("nats://token@a:1,nats://b:2") == "nats://xxxxx@a:1,nats://b:2"
    assert redact_url("host:4222") == "host:4222"


def test_env_prefix_normalizes_instance_name() -> None:
    assert env_prefix("hub") == "HUB"
    assert env_prefix("hub-v2.eu") == "HUB_V2_EU"
    assert DEFAULT_INSTANCE == "default"


def test_zero_means_kit_default() -> None:
    assert positive_or(0.0, 10.0) == 10.0
    assert positive_or(3.0, 10.0) == 3.0
    assert positive_int_or(0, 100) == 100
    assert positive_int_or(7, 100) == 7


def test_nested_configs_are_standalone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NATS_MAX_IN_FLIGHT", "3")
    monkeypatch.setenv("NATS_JS_MAX_DELIVER", "4")

    assert BackpressureConfig().max_in_flight == 3
    assert JetStreamConfig().max_deliver == 4


def test_direct_construction_validates() -> None:
    with pytest.raises(ValueError, match="NATS_URL"):
        Config(url="")


def test_config_without_env_rejects_unknown_and_misplaced_fields() -> None:
    cfg = config_without_env(url="nats://h:4222", jetstream={"stream_names": "ORDERS"})
    assert cfg.jetstream.stream_names == ("ORDERS",)

    # Поле вложенной секции на верхнем уровне молча не применилось бы.
    with pytest.raises(ConfigError, match="unknown field 'stream_names'"):
        config_without_env(stream_names="ORDERS")
    with pytest.raises(ConfigError, match=r"unknown field 'jetstream\.enable'"):
        config_without_env(jetstream={"enable": True})


def test_parse_retention() -> None:
    assert parse_retention("limits").value == "limits"
    assert parse_retention("").value == "limits"
    assert parse_retention("interest").value == "interest"
    assert parse_retention("work_queue").value == "workqueue"
    with pytest.raises(ConfigError, match="NATS_JS_RETENTION"):
        parse_retention("nope")


@pytest.mark.parametrize(
    ("filter_", "subject", "matches"),
    [
        ("orders.created", "orders.created", True),
        ("orders.*", "orders.created", True),
        ("orders.*", "orders.created.v2", False),
        ("orders.>", "orders.created.v2", True),
        ("orders.>", "orders", False),
        ("orders.*.created", "orders.42.created", True),
        ("orders", "orders.created", False),
    ],
)
def test_subject_matches(filter_: str, subject: str, matches: bool) -> None:
    assert subject_matches(filter_, subject) is matches


def test_subject_and_name_validation() -> None:
    assert valid_publish_subject("dlq.orders")
    assert not valid_publish_subject("dlq.*")
    assert not valid_publish_subject("dlq..orders")
    assert not valid_publish_subject("")
    assert valid_stream_name("ORDERS")
    assert not valid_stream_name("ORDERS.v2")
    assert not valid_stream_name("")
    assert valid_instance_name("hub-2.eu")
    assert not valid_instance_name("hub/v2")
