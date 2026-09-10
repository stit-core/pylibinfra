from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pyotp
import pytest
from pydantic import ValidationError

from pylibinfra.authnkit import (
    MAX_TOTP_SKEW,
    TOTP,
    TOTP_PERIOD,
    CacheUsedCodeStore,
    ConfigError,
    InvalidSecretError,
    MemoryUsedCodeStore,
    TOTPConfig,
    UsedCodeCache,
    UsedCodeStore,
    UsedCodeStoreError,
)
from pylibinfra.clockkit import Fixed, Tickable

NOW = datetime.fromtimestamp(1_700_000_000, UTC)


def _totp(**kwargs: Any) -> TOTP:
    cfg = kwargs.pop("cfg", None) or TOTPConfig(issuer="TestIssuer", skew=1)
    return TOTP(cfg, **kwargs)


def _code(secret: str, when: datetime) -> str:
    return pyotp.TOTP(secret, digits=6, digest=hashlib.sha1, interval=TOTP_PERIOD).at(when)


class _FailingStore:
    async def mark_used(self, key: str, ttl: float) -> bool:
        raise RuntimeError("store down")


class _FakeCache:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes, float]] = []
        self._keys: set[str] = set()

    async def set_nx(self, key: str, value: bytes, ttl: float) -> bool:
        self.calls.append((key, value, ttl))
        if key in self._keys:
            return False
        self._keys.add(key)
        return True


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"issuer": ""}, id="empty issuer"),
        pytest.param({"issuer": "My:Service"}, id="issuer with colon"),
        pytest.param({"issuer": "My/Service"}, id="issuer with slash"),
        pytest.param({"skew": MAX_TOTP_SKEW + 1}, id="skew above window"),
        pytest.param({"skew": -1}, id="negative skew"),
    ],
)
def test_config_rejects_invalid_values(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TOTPConfig(**{"issuer": "svc", **overrides})


def test_config_accepts_issuer_with_space() -> None:
    cfg = TOTPConfig(issuer="My Service", skew=MAX_TOTP_SKEW)
    assert cfg.issuer == "My Service"
    assert cfg.skew == MAX_TOTP_SKEW


def test_constructor_rejects_ambiguous_issuer() -> None:
    raw = TOTPConfig.model_construct(issuer="My:Service", skew=1)
    with pytest.raises(ConfigError):
        TOTP(raw)


def test_generate_and_verify() -> None:
    gen = _totp(clock=Fixed(NOW))
    secret, url = gen.generate("user@example.com")

    assert secret
    assert url
    assert gen.verify(secret, _code(secret, NOW)) is True


def test_generate_url_is_readable_by_authenticators() -> None:
    gen = _totp(cfg=TOTPConfig(issuer="My Service", skew=1))
    secret, url = gen.generate("user@example.com")

    parsed = urlparse(url)
    assert parsed.scheme == "otpauth"
    assert parsed.netloc == "totp"
    assert parsed.path == "/My%20Service:user%40example.com"
    query = parse_qs(parsed.query)
    assert query["secret"] == [secret]
    assert query["issuer"] == ["My Service"]
    # Период 30 с, 6 цифр и SHA1 — дефолты otpauth, pyotp их не дублирует в URL.
    assert "period" not in query
    assert "digits" not in query


@pytest.mark.parametrize("account", ["", "us/er", "us:er"])
def test_generate_rejects_ambiguous_account(account: str) -> None:
    with pytest.raises(ConfigError):
        _totp().generate(account)


def test_invalid_code_and_secret() -> None:
    gen = _totp(clock=Fixed(NOW))
    secret, _ = gen.generate("u")

    assert gen.verify(secret, "000000") is False
    assert gen.verify(secret, "") is False

    with pytest.raises(InvalidSecretError):
        gen.verify("not-base32-secret!!", "123456")


def test_skew_window() -> None:
    clock = Fixed(NOW)
    secret, _ = _totp().generate("u")
    previous = _code(secret, NOW - timedelta(seconds=TOTP_PERIOD))

    lenient = _totp(cfg=TOTPConfig(issuer="TestIssuer", skew=1), clock=clock)
    assert lenient.verify(secret, previous) is True

    strict = _totp(cfg=TOTPConfig(issuer="TestIssuer", skew=0), clock=clock)
    assert strict.verify(secret, previous) is False


def test_code_ttl_covers_the_window() -> None:
    assert _totp(cfg=TOTPConfig(issuer="s", skew=0)).code_ttl == TOTP_PERIOD
    assert _totp(cfg=TOTPConfig(issuer="s", skew=1)).code_ttl == 3 * TOTP_PERIOD
    assert _totp(cfg=TOTPConfig(issuer="s", skew=2)).code_ttl == 5 * TOTP_PERIOD


async def test_verify_once_rejects_replay() -> None:
    clock = Tickable(NOW)
    store = MemoryUsedCodeStore(clock=clock)
    gen = _totp(clock=clock, used_codes=store)

    secret, _ = gen.generate("u")
    code = _code(secret, NOW)

    assert await gen.verify_once(secret, code) is True
    assert await gen.verify_once(secret, code) is False

    # Чужая запись не блокирует другой секрет с тем же кодом.
    other, _ = gen.generate("u2")
    assert await gen.verify_once(other, _code(other, NOW)) is True

    # Неверный код до хранилища не доходит.
    assert await gen.verify_once(secret, "000000") is False


async def test_used_code_entry_expires() -> None:
    clock = Tickable(NOW)
    store = MemoryUsedCodeStore(clock=clock)

    assert await store.mark_used("k", 60) is True
    assert await store.mark_used("k", 60) is False

    clock.advance(61)
    assert await store.mark_used("k", 60) is True


async def test_verify_once_requires_store() -> None:
    gen = _totp(clock=Fixed(NOW))
    secret, _ = gen.generate("u")
    with pytest.raises(ConfigError):
        await gen.verify_once(secret, _code(secret, NOW))


async def test_verify_once_store_error() -> None:
    gen = _totp(clock=Fixed(NOW), used_codes=_FailingStore())
    secret, _ = gen.generate("u")

    with pytest.raises(UsedCodeStoreError) as exc:
        await gen.verify_once(secret, _code(secret, NOW))
    assert isinstance(exc.value.__cause__, RuntimeError)


async def test_cache_used_code_store() -> None:
    cache = _FakeCache()
    store: UsedCodeStore = CacheUsedCodeStore(cache)
    assert isinstance(cache, UsedCodeCache)

    assert await store.mark_used("key", 90) is True
    assert await store.mark_used("key", 90) is False
    assert cache.calls[0] == ("key", b"1", 90)


async def test_used_code_key_hides_the_secret() -> None:
    cache = _FakeCache()
    gen = _totp(clock=Fixed(NOW), used_codes=CacheUsedCodeStore(cache))
    secret, _ = gen.generate("u")
    code = _code(secret, NOW)

    await gen.verify_once(secret, code)

    key, _, ttl = cache.calls[0]
    assert key.startswith("authnkit:totp:")
    assert secret not in key
    assert code not in key
    assert ttl == gen.code_ttl


async def test_metrics(read_metric: Callable[..., float]) -> None:
    clock = Fixed(NOW)
    gen = _totp(clock=clock, used_codes=MemoryUsedCodeStore(clock=clock))
    secret, _ = gen.generate("u")
    code = _code(secret, NOW)

    gen.verify(secret, code)
    gen.verify(secret, "000000")
    with pytest.raises(InvalidSecretError):
        gen.verify("not-base32!!", code)
    await gen.verify_once(secret, code)
    await gen.verify_once(secret, code)

    metric = "authn_totp_verifications_total"
    assert read_metric(metric, {"result": "ok"}) == 2
    assert read_metric(metric, {"result": "invalid"}) == 1
    assert read_metric(metric, {"result": "secret_invalid"}) == 1
    assert read_metric(metric, {"result": "replay"}) == 1

    failing = TOTP(TOTPConfig(issuer="TestIssuer"), clock=clock, used_codes=_FailingStore())
    with pytest.raises(UsedCodeStoreError):
        await failing.verify_once(secret, code)
    assert read_metric(metric, {"result": "store_error"}) == 1
