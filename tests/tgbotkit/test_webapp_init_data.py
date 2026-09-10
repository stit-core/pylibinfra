"""WebApp initData: подпись, свежесть и отказ на любой правке полей."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from urllib.parse import urlencode

import pytest
from pydantic import SecretStr

from pylibinfra.clockkit import Fixed
from pylibinfra.tgbotkit import InitDataError, validate_init_data

TOKEN = "123456:ABCdefGHI"
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def make_init_data(fields: dict[str, str], token: str = TOKEN) -> str:
    check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": signature})


def _fresh(**extra: str) -> str:
    return make_init_data({"auth_date": str(int(NOW.timestamp())), "user": '{"id":1}', **extra})


def test_valid_init_data_returns_fields() -> None:
    parsed = validate_init_data(_fresh(query_id="AAH"), TOKEN, clock=Fixed(NOW))
    assert parsed["user"] == '{"id":1}'
    assert parsed["query_id"] == "AAH"
    assert "hash" not in parsed


@pytest.mark.parametrize("token", ["", SecretStr("")])
def test_empty_token_rejected(token: str | SecretStr) -> None:
    # Ключ от пустого токена вычислит кто угодно: пустой BOT_TOKEN обязан быть отказом,
    # а не «проверка прошла».
    with pytest.raises(InitDataError, match="bot token is empty"):
        validate_init_data(_fresh(), token, clock=Fixed(NOW))


def test_secret_str_token_is_accepted() -> None:
    parsed = validate_init_data(_fresh(), SecretStr(TOKEN), clock=Fixed(NOW))
    assert parsed["user"] == '{"id":1}'


def test_wrong_token_rejected() -> None:
    with pytest.raises(InitDataError, match="hash mismatch"):
        validate_init_data(_fresh(), "999999:WRONG", clock=Fixed(NOW))


def test_tampered_field_rejected() -> None:
    tampered = _fresh().replace("%7B%22id%22%3A1%7D", "%7B%22id%22%3A2%7D")
    with pytest.raises(InitDataError, match="hash mismatch"):
        validate_init_data(tampered, TOKEN, clock=Fixed(NOW))


def test_missing_hash_rejected() -> None:
    with pytest.raises(InitDataError, match="no hash"):
        validate_init_data("auth_date=123&user=x", TOKEN)


def test_missing_auth_date_rejected() -> None:
    data = make_init_data({"user": '{"id":1}'})
    with pytest.raises(InitDataError, match="no auth_date"):
        validate_init_data(data, TOKEN, clock=Fixed(NOW))


def test_expired_rejected() -> None:
    old = str(int(NOW.timestamp()) - 3600)
    data = make_init_data({"auth_date": old, "user": '{"id":1}'})
    with pytest.raises(InitDataError, match="expired"):
        validate_init_data(data, TOKEN, max_age=300, clock=Fixed(NOW))


def test_max_age_zero_skips_freshness() -> None:
    old = str(int(NOW.timestamp()) - 3600)
    data = make_init_data({"auth_date": old, "user": '{"id":1}'})
    assert validate_init_data(data, TOKEN, max_age=0)["user"] == '{"id":1}'


def test_real_clock_is_the_default() -> None:
    now = str(int(datetime.now(UTC).timestamp()))
    data = make_init_data({"auth_date": now, "user": '{"id":1}'})
    assert validate_init_data(data, TOKEN)["auth_date"] == now
