"""Разбор NATS-субъектов: соответствие фильтру подписки и допустимость имён."""

from __future__ import annotations

_WILDCARDS = ("*", ">")
_FORBIDDEN_IN_SUBJECT = (" ", "\t", "\r", "\n", *_WILDCARDS)
_FORBIDDEN_IN_STREAM = (".", " ", "\t", "*", ">", "/", "\\")


def subject_matches(filter_: str, subject: str) -> bool:
    """Накрывает ли фильтр (с `*` и `>`) конкретный subject — семантика фильтра подписки NATS."""
    want = filter_.split(".")
    have = subject.split(".")
    for i, token in enumerate(want):
        if token == ">":  # noqa: S105
            return i < len(have)
        if i >= len(have):
            return False
        if token not in ("*", have[i]):
            return False
    return len(want) == len(have)


def valid_publish_subject(subject: str) -> bool:
    """Subject, в который можно опубликовать: непустые токены, без пробелов и wildcard'ов."""
    if not subject or any(ch in subject for ch in _FORBIDDEN_IN_SUBJECT):
        return False
    return all(token for token in subject.split("."))


def valid_stream_name(name: str) -> bool:
    """Имя стрима JetStream: непустое, без точек, пробелов, wildcard'ов и слэшей."""
    if not name:
        return False
    return not any(ch in name for ch in _FORBIDDEN_IN_STREAM)


def valid_instance_name(name: str) -> bool:
    """Имя инстанса шины: буквы, цифры, `_`, `-`, `.` — из него собирается ENV-префикс."""
    return all(ch.isascii() and (ch.isalnum() or ch in "_-.") for ch in name)
