"""Request-scoped context: request_id хранится в contextvar — logkit
(merge_contextvars) и httpclient читают его без проброса в сигнатуры (аналог
проброса request_id через context.Context в golibinfra).
"""

from contextvars import ContextVar

HEADER_REQUEST_ID = "X-Request-ID"

_request_id: ContextVar[str] = ContextVar("httpkit_request_id", default="")


def request_id() -> str:
    return _request_id.get()


def set_request_id(value: str) -> object:
    return _request_id.set(value)


def reset_request_id(token: object) -> None:
    _request_id.reset(token)  # type: ignore[arg-type]
