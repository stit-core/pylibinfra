"""SecurityHeadersConfig + SecurityHeadersMiddleware: выставляет HSTS,
X-Content-Type-Options, X-Frame-Options, Referrer-Policy, опц. CSP на каждый
ответ. Пустое значение поля → заголовок не ставится.
"""

from collections.abc import Awaitable, Callable

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class SecurityHeadersConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    hsts: str = Field(default="max-age=31536000; includeSubDomains", validation_alias="HTTP_HSTS")
    content_type_options: str = Field(default="nosniff")
    frame_options: str = Field(default="DENY")
    referrer_policy: str = Field(default="strict-origin-when-cross-origin")
    csp: str = Field(default="", validation_alias="HTTP_CSP")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, cfg: SecurityHeadersConfig) -> None:
        self._app = app
        self._headers: list[tuple[bytes, bytes]] = []
        pairs = (
            ("strict-transport-security", cfg.hsts),
            ("x-content-type-options", cfg.content_type_options),
            ("x-frame-options", cfg.frame_options),
            ("referrer-policy", cfg.referrer_policy),
            ("content-security-policy", cfg.csp),
        )
        for name, value in pairs:
            if value:
                self._headers.append((name.encode("latin-1"), value.encode("latin-1")))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        send_wrapped = self._wrap(send)
        await self._app(scope, receive, send_wrapped)

    def _wrap(self, send: Send) -> Callable[[Message], Awaitable[None]]:
        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.extend(self._headers)
            await send(message)

        return send_wrapper
