"""validate_init_data — проверка Telegram WebApp initData (pure). Алгоритм:
secret = HMAC-SHA256(key="WebAppData", msg=bot_token); ожидаемый hash =
HMAC-SHA256(key=secret, msg=data_check_string), где data_check_string —
отсортированные "key=value" (без hash) через \n. Сверка constant-time + проверка
свежести auth_date. Возвращает разобранные поля или кидает InitDataError.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import parse_qsl


class InitDataError(ValueError):
    pass


def validate_init_data(init_data: str, bot_token: str, *, max_age: int = 300) -> dict[str, str]:
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise InitDataError("init data has no hash")

    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise InitDataError("init data hash mismatch")

    if max_age > 0:
        auth_date = pairs.get("auth_date", "")
        if not auth_date.isdigit():
            raise InitDataError("init data has no auth_date")
        if int(time.time()) - int(auth_date) > max_age:
            raise InitDataError("init data expired")

    return pairs
