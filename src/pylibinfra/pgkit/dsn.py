"""Редактирование строки подключения: пароль не уходит в тексты ошибок и логи.

Драйверы кладут DSN в сообщение целиком — yoyo на URI без схемы
(`No scheme specified in connection URI '<dsn>'`), psycopg2 на неразбираемой
keyword-строке (`invalid dsn: missing "=" after "<dsn>"`). Поэтому текст чужого
исключения проходит через `safe_error` до того, как попасть в ошибку кита.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

# Маска вместо пароля: та же строка, что подставляет natskit в URL брокера.
REDACTED = "xxxxx"

# keyword-форма DSN (`host=… password=…`): её принимает psycopg2, и в его
# сообщениях об ошибке она появляется как есть.
_KEYWORD_PASSWORD_RE = re.compile(r"(?i)(\bpassword\s*=\s*)('(?:[^'\\]|\\.)*'|\S+)")


def redact_dsn(raw: str) -> str:
    """Прячет пароль строки подключения: `postgresql://user:xxxxx@host:5432/db`.

    Понимает обе формы (URI с userinfo и keyword-строку `password=…`), в том
    числе испорченную оператором — редактировать нужно как раз её, потому что
    именно она попадает в текст ошибки драйвера. Имя пользователя, хост и база
    остаются: по ним оператор узнаёт своё подключение.
    """
    text = _KEYWORD_PASSWORD_RE.sub(rf"\1{REDACTED}", raw)
    scheme, sep, rest = text.partition("://")
    if not sep:
        scheme, rest = "", text
    at = rest.rfind("@")
    if at < 0:
        return text
    user, colon, _ = rest[:at].partition(":")
    if not colon:
        return text
    return f"{scheme}{sep}{user}:{REDACTED}@{rest[at + 1 :]}"


def safe_error(dsn: str, exc: BaseException) -> str:
    """Текст исключения без строки подключения и без пароля из неё."""
    text = str(exc)
    if not dsn:
        return text
    text = text.replace(dsn, redact_dsn(dsn))
    for secret in _passwords(dsn):
        text = text.replace(secret, REDACTED)
    return text


def _passwords(dsn: str) -> tuple[str, ...]:
    """Пароль DSN в записанном и percent-декодированном виде; пусто — пароля нет."""
    match = _KEYWORD_PASSWORD_RE.search(dsn)
    if match is not None:
        raw = match.group(2).strip("'")
    else:
        _, sep, rest = dsn.partition("://")
        if not sep:
            rest = dsn
        at = rest.rfind("@")
        raw = rest[:at].partition(":")[2] if at >= 0 else ""
    if not raw:
        return ()
    decoded = unquote(raw)
    return (raw,) if decoded == raw else (raw, decoded)
