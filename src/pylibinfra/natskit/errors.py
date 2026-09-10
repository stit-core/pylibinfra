"""Ошибки natskit: невалидный конфиг, закрытое соединение, сбой публикации, маркер «не ретраить»."""

from __future__ import annotations

from pylibinfra._internal import configkit


class NatskitError(Exception):
    """База ошибок кита (кроме `ConfigError` — та наследует `ValueError`)."""


class ConfigError(configkit.ConfigError):
    """Невалидный конфиг инстанса шины: валит старт сервиса, а не первую публикацию."""


class ClosedError(NatskitError):
    """Подписка на инстанс, у которого уже начался `Conn.close()`."""

    def __init__(self) -> None:
        super().__init__("natskit: closed")


class ConnectionClosedError(NatskitError):
    """Соединение закрыто окончательно (исчерпаны reconnect'ы или вызван close)."""


class PublishError(NatskitError):
    """Публикация не удалась: сообщение в стрим/шину не ушло."""


class SubscribeError(NatskitError):
    """Подписку не удалось зарегистрировать (транспорт, консьюмер JetStream)."""


class StreamError(NatskitError):
    """Стрим не создан, не найден или его конфиг не удалось сверить."""


class JetStreamDisabledError(NatskitError):
    """Обращение к `StreamBus` при `NATS_JETSTREAM_ENABLED=false`."""

    def __init__(self) -> None:
        super().__init__("natskit: jetstream is disabled")


class TerminalError(NatskitError):
    """Маркер «не ретраить»: JetStream отправляет такое сообщение в DLQ/term с первой попытки."""


def is_terminal(exc: BaseException | None) -> bool:
    """True, если `exc` сам `TerminalError` или обёрнут им по цепочке `raise ... from`."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, TerminalError):
            return True
        seen.add(id(exc))
        exc = exc.__cause__
    return False
