import hashlib
import hmac
import time
from urllib.parse import urlencode

import pytest

from pylibinfra.tgbotkit import InitDataError, validate_init_data

TOKEN = "123456:ABCdefGHI"


def make_init_data(fields: dict[str, str], token: str = TOKEN) -> str:
    dcs = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": h})


def test_valid_init_data_returns_fields() -> None:
    now = str(int(time.time()))
    data = make_init_data({"auth_date": now, "user": '{"id":1}', "query_id": "AAH"})
    parsed = validate_init_data(data, TOKEN)
    assert parsed["user"] == '{"id":1}'
    assert parsed["query_id"] == "AAH"
    assert "hash" not in parsed


def test_wrong_token_rejected() -> None:
    now = str(int(time.time()))
    data = make_init_data({"auth_date": now, "user": '{"id":1}'})
    with pytest.raises(InitDataError):
        validate_init_data(data, "999999:WRONG")


def test_missing_hash_rejected() -> None:
    with pytest.raises(InitDataError):
        validate_init_data("auth_date=123&user=x", TOKEN)


def test_expired_rejected() -> None:
    old = str(int(time.time()) - 3600)
    data = make_init_data({"auth_date": old, "user": '{"id":1}'})
    with pytest.raises(InitDataError):
        validate_init_data(data, TOKEN, max_age=300)


def test_max_age_zero_skips_freshness() -> None:
    old = str(int(time.time()) - 3600)
    data = make_init_data({"auth_date": old, "user": '{"id":1}'})
    parsed = validate_init_data(data, TOKEN, max_age=0)
    assert parsed["user"] == '{"id":1}'
