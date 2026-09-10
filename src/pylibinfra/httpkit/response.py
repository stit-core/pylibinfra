"""ResponseTap — единственная обёртка `send` на запрос: статус, объём тела и заголовки кита."""

from __future__ import annotations

from starlette.types import Message, Scope, Send

_TAP_KEY = "pylibinfra.httpkit.tap"

BODY_OVERFLOW_KEY = "pylibinfra.httpkit.body_overflow"
"""Ключ scope: тело запроса превысило лимит — ответ обязан быть 413, кто бы его ни писал."""


class ResponseTap:
    """Обёртка ASGI-`send`: считает статус и байты, применяет заголовки, зарегистрированные китом.

    Заголовки регистрируются на пути запроса (`set_header`/`add_header`) и применяются в
    `http.response.start` — так их получает любой ответ, включая нейтральный 500 от Recovery
    и 429 лимитера. Повторный `http.response.start` игнорируется.
    """

    __slots__ = ("_add", "_send", "_set", "body_bytes", "headers", "started", "status")

    def __init__(self, send: Send) -> None:
        """Оборачивает `send`; до первого ответа статус — 200."""
        self._send = send
        self._set: dict[bytes, bytes] = {}
        self._add: list[tuple[bytes, bytes]] = []
        self.status = 200
        self.body_bytes = 0
        self.started = False
        self.headers: list[tuple[bytes, bytes]] = []

    def set_header(self, name: str, value: str) -> None:
        """Ставит заголовок ответа, вытесняя одноимённые."""
        self._set[name.lower().encode("latin-1")] = value.encode("latin-1")

    def add_header(self, name: str, value: str) -> None:
        """Добавляет заголовок ответа, не трогая одноимённые (`Vary`)."""
        self._add.append((name.lower().encode("latin-1"), value.encode("latin-1")))

    async def send(self, message: Message) -> None:
        """Пропускает сообщение наружу, попутно фиксируя статус, байты и заголовки."""
        if message["type"] == "http.response.start":
            if self.started:
                return
            self.started = True
            self.status = int(message["status"])
            message = {**message, "headers": self._merge(message.get("headers", []))}
            self.headers = list(message["headers"])
        elif message["type"] == "http.response.body":
            self.body_bytes += len(message.get("body", b"") or b"")
        await self._send(message)

    def _merge(self, headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
        merged = [*headers, *self._add]
        for name, value in self._set.items():
            merged = [(n, v) for n, v in merged if n.lower() != name]
            merged.append((name, value))
        return merged


def tap(scope: Scope, send: Send) -> ResponseTap:
    """Обёртка запроса: существующая из scope либо новая поверх `send`."""
    existing = scope.get(_TAP_KEY)
    if isinstance(existing, ResponseTap):
        return existing
    created = ResponseTap(send)
    scope[_TAP_KEY] = created
    return created


def header(scope: Scope, name: str) -> str:
    """Первое значение заголовка запроса; пустая строка, если его нет."""
    wanted = name.lower().encode("latin-1")
    for key, value in scope.get("headers", ()):
        if key.lower() == wanted:
            return bytes(value).decode("latin-1")
    return ""


def header_values(scope: Scope, name: str) -> list[str]:
    """Все значения заголовка запроса в порядке получения."""
    wanted = name.lower().encode("latin-1")
    return [
        bytes(value).decode("latin-1")
        for key, value in scope.get("headers", ())
        if key.lower() == wanted
    ]
