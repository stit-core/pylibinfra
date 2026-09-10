"""handlertest — тестовое FastAPI-приложение поверх ASGI-транспорта и ассерты по ответу.

Хелперы для тестов httpkit-консьюмера: `new_app` + `new_client` (запрос без сети),
`decode_api_error`/`assert_api_error` понимают и problem+json httpkit, и ошибки валидации.
"""

from .client import (
    APIError,
    APIErrorLocation,
    assert_api_error,
    assert_json,
    assert_status,
    decode_api_error,
    new_app,
    new_client,
)

__all__ = [
    "APIError",
    "APIErrorLocation",
    "assert_api_error",
    "assert_json",
    "assert_status",
    "decode_api_error",
    "new_app",
    "new_client",
]
