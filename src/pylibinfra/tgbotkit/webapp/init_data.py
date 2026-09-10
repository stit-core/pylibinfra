"""validate_init_data — проверка подписи и свежести Telegram WebApp `initData`."""

from __future__ import annotations

import hashlib
import hmac
from urllib.parse import parse_qsl

from pydantic import SecretStr

from pylibinfra.clockkit import Clock, Real
from pylibinfra.tgbotkit.errors import InitDataError

DEFAULT_MAX_AGE = 300


def validate_init_data(
    init_data: str,
    bot_token: SecretStr | str,
    *,
    max_age: int = DEFAULT_MAX_AGE,
    clock: Clock | None = None,
) -> dict[str, str]:
    """Возвращает разобранные поля `initData` или бросает `InitDataError`.

    Подпись: `HMAC-SHA256` от отсортированных `key=value` через `\\n` на ключе
    `HMAC-SHA256("WebAppData", bot_token)`, сверка constant-time. `max_age` в секундах
    ограничивает возраст `auth_date`; `0` отключает проверку свежести (и открывает
    бесконечный replay — снимать её стоит только в тестах). Пустой `bot_token`
    отвергается: ключ от пустого токена вычислит кто угодно.
    """
    token = bot_token.get_secret_value() if isinstance(bot_token, SecretStr) else bot_token
    if not token:
        raise InitDataError("tgbotkit: bot token is empty")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", "")
    if not received:
        raise InitDataError("tgbotkit: init data has no hash")

    data_check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    # Сверяем байты: на строке с не-ASCII (а `hash` приходит от клиента)
    # compare_digest бросает TypeError вместо честного отказа.
    if not hmac.compare_digest(expected.encode(), received.encode()):
        raise InitDataError("tgbotkit: init data hash mismatch")

    if max_age > 0:
        _check_freshness(
            pairs.get("auth_date", ""), max_age, clock if clock is not None else Real()
        )
    return pairs


def _check_freshness(auth_date: str, max_age: int, clock: Clock) -> None:
    if not auth_date.isdigit():
        raise InitDataError("tgbotkit: init data has no auth_date")
    # Метку из будущего Telegram не подписывает: это часы бэкенда ушли назад, и
    # окно свежести молча растянулось бы на всю величину расхождения.
    if abs(clock.now().timestamp() - int(auth_date)) > max_age:
        raise InitDataError("tgbotkit: init data expired")
