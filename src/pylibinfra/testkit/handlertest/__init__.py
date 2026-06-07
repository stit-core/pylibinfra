"""handlertest — тестовое FastAPI-приложение + httpx ASGI-клиент (без сети) и
ассерт-хелперы по APIError/JSON-ответу для тестов httpkit-консьюмера."""

from .client import (
    APIError,
    assert_api_error,
    assert_json,
    assert_status,
    decode_api_error,
    new_app,
    new_client,
)

__all__ = [
    "APIError",
    "assert_api_error",
    "assert_json",
    "assert_status",
    "decode_api_error",
    "new_app",
    "new_client",
]
