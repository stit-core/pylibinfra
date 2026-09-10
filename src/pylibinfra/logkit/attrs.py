"""Реестр ctx-полей: значения, которые kit добавляет в каждую запись обоих sink'ов."""

from __future__ import annotations

import threading
from collections.abc import Callable

Attr = Callable[[], object | None]

_lock = threading.Lock()
# Чтение — на каждой записи, поэтому copy-on-write: снимок читается без lock'а.
_registered: tuple[tuple[str, Attr], ...] = ()


def register_attr(key: str, fn: Attr) -> None:
    """Регистрирует поле `key`, значение которого берётся из контекста на каждой записи.

    `fn` возвращает `None` — поле не добавляется. Повторная регистрация того же
    ключа заменяет прежнюю функцию, поэтому дважды собранный граф не даёт дублей.
    """
    if not key:
        raise ValueError("logkit: register_attr: empty key")
    if not callable(fn):
        raise ValueError("logkit: register_attr: fn is not callable")

    global _registered
    with _lock:
        replaced = tuple((k, fn if k == key else f) for k, f in _registered)
        _registered = replaced if any(k == key for k, _ in replaced) else (*replaced, (key, fn))


def registered() -> tuple[tuple[str, Attr], ...]:
    """Снимок реестра в порядке регистрации."""
    return _registered


def _reset() -> None:
    global _registered
    with _lock:
        _registered = ()
