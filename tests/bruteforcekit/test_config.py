import pytest

from pylibinfra.bruteforcekit import (
    FULL_IPV6_PREFIX,
    MAX_SUBJECT_LEN,
    Config,
    ConfigError,
    InvalidKeyError,
    new_config,
)

_ENV = (
    "BRUTEFORCE_MAX_FAILURES",
    "BRUTEFORCE_LOCK_DURATION",
    "BRUTEFORCE_CLEANUP_RETENTION",
    "BRUTEFORCE_CLEANUP_INTERVAL",
    "BRUTEFORCE_IPV6_PREFIX",
    "MAX_FAILURES",
    "LOCK_DURATION",
    "CLEANUP_RETENTION",
    "CLEANUP_INTERVAL",
    "IPV6_PREFIX",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)


def test_defaults() -> None:
    cfg = new_config()
    assert cfg.max_failures == 5
    assert cfg.lock_duration == 900.0
    assert cfg.cleanup_retention == 86400.0
    assert cfg.cleanup_interval == 300.0
    assert cfg.ipv6_prefix == FULL_IPV6_PREFIX


def test_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRUTEFORCE_MAX_FAILURES", "10")
    monkeypatch.setenv("BRUTEFORCE_LOCK_DURATION", "1800")
    monkeypatch.setenv("BRUTEFORCE_CLEANUP_RETENTION", "172800")
    monkeypatch.setenv("BRUTEFORCE_CLEANUP_INTERVAL", "60")
    monkeypatch.setenv("BRUTEFORCE_IPV6_PREFIX", "64")
    cfg = new_config()
    assert cfg.max_failures == 10
    assert cfg.lock_duration == 1800.0
    assert cfg.cleanup_retention == 172800.0
    assert cfg.cleanup_interval == 60.0
    assert cfg.ipv6_prefix == 64


@pytest.mark.parametrize(
    ("env", "value", "message"),
    [
        ("BRUTEFORCE_MAX_FAILURES", "0", "BRUTEFORCE_MAX_FAILURES must be >= 1"),
        ("BRUTEFORCE_LOCK_DURATION", "0", "BRUTEFORCE_LOCK_DURATION must be > 0"),
        ("BRUTEFORCE_CLEANUP_RETENTION", "-1", "BRUTEFORCE_CLEANUP_RETENTION must be >= 0"),
        ("BRUTEFORCE_CLEANUP_INTERVAL", "0", "BRUTEFORCE_CLEANUP_INTERVAL must be > 0"),
        ("BRUTEFORCE_IPV6_PREFIX", "0", r"BRUTEFORCE_IPV6_PREFIX must be in \[1, 128\]"),
        ("BRUTEFORCE_IPV6_PREFIX", "129", r"BRUTEFORCE_IPV6_PREFIX must be in \[1, 128\]"),
    ],
)
def test_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch, env: str, value: str, message: str
) -> None:
    monkeypatch.setenv(env, value)
    with pytest.raises(ConfigError, match=message):
        new_config()


def test_zero_retention_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRUTEFORCE_CLEANUP_RETENTION", "0")
    assert new_config().cleanup_retention == 0.0


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("203.0.113.7", "203.0.113.7"),
        ("::ffff:203.0.113.7", "203.0.113.7"),
        ("::1", "::1"),
        ("0:0:0:0:0:0:0:1", "::1"),
        ("2001:DB8::1", "2001:db8::1"),
        ("fe80::1%eth0", "fe80::1"),
    ],
)
def test_normalize_key_canonicalizes_ip(raw: str, canonical: str) -> None:
    cfg = Config.model_validate({})
    assert cfg.normalize_key("user@example.com", raw) == ("user@example.com", canonical)


def test_normalize_key_aggregates_ipv6_by_prefix() -> None:
    cfg = Config.model_validate({"ipv6_prefix": 64})
    _, first = cfg.normalize_key("u", "2001:db8:0:1::5")
    _, second = cfg.normalize_key("u", "2001:db8:0:1:ffff:ffff:ffff:ffff")
    assert first == second == "2001:db8:0:1::"


def test_ipv6_prefix_does_not_touch_ipv4() -> None:
    cfg = Config.model_validate({"ipv6_prefix": 64})
    assert cfg.normalize_key("u", "203.0.113.7")[1] == "203.0.113.7"


def test_normalize_key_keeps_subject_as_is() -> None:
    cfg = Config.model_validate({})
    assert cfg.normalize_key("User@Example.com", "1.2.3.4")[0] == "User@Example.com"


@pytest.mark.parametrize(
    ("subject", "ip", "message"),
    [
        ("", "1.2.3.4", "subject is empty"),
        ("x" * (MAX_SUBJECT_LEN + 1), "1.2.3.4", "subject is longer"),
        ("u", "", "ip is empty"),
        ("u", "203.0.113.7:443", "is not an IP address"),
        ("u", "203.0.113.0/24", "is not an IP address"),
        ("u", "localhost", "is not an IP address"),
    ],
)
def test_normalize_key_rejects_bad_keys(subject: str, ip: str, message: str) -> None:
    cfg = Config.model_validate({})
    with pytest.raises(InvalidKeyError, match=message):
        cfg.normalize_key(subject, ip)


def test_subject_at_limit_is_accepted() -> None:
    cfg = Config.model_validate({})
    subject = "x" * MAX_SUBJECT_LEN
    assert cfg.normalize_key(subject, "1.2.3.4")[0] == subject
