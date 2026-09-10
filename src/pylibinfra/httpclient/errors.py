"""Ошибки httpclient: конфиг, разомкнутая цепь, политика редиректов, SSE."""

from __future__ import annotations

from pylibinfra._internal import configkit


class HttpClientError(Exception):
    """База ошибок, которые клиент поднимает сам (не транспорт httpx и не конфиг)."""


class ConfigError(configkit.ConfigError):
    """Невалидный конфиг клиента."""


class BreakerOpenError(HttpClientError):
    """Цепь хоста разомкнута: запрос не отправлен."""


class TooManyRedirectsError(HttpClientError):
    """Цепочка редиректов длиннее `Config.max_redirects`."""


class CrossHostRedirectError(HttpClientError):
    """Редирект уводит на другой хост при `allow_cross_host_redirects=False`."""


class NotEventStreamError(HttpClientError):
    """Ответ на `stream` пришёл не с `Content-Type: text/event-stream`."""


class EventTooLargeError(HttpClientError):
    """Строка или событие SSE превысили `Config.sse_max_event_bytes`."""


class HeaderTimeoutError(HttpClientError):
    """Заголовки ответа на `stream` не пришли за `Config.timeout`."""


class StatusError(HttpClientError):
    """Неуспешный статус там, где ответ нельзя отдать вызывающему (`stream`)."""

    def __init__(self, status: int, body: str = "") -> None:
        message = f"httpclient: sse: status {status}"
        if body:
            message = f"{message}: {body}"
        super().__init__(message)
        self.status = status
        self.body = body
