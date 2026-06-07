"""CORSConfig + apply_cors: навешивает starlette/fastapi CORSMiddleware с
origins/methods/headers/max-age из конфига. Пустой список origins → middleware
не подключается (CORS отключён, как в golibinfra).
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


class CORSConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    # запятая-разделённые списки (env передаёт строкой)
    origins: str = Field(default="", validation_alias="HTTP_CORS_ORIGINS")
    methods: str = Field(
        default="GET,POST,PUT,PATCH,DELETE,OPTIONS", validation_alias="HTTP_CORS_METHODS"
    )
    headers: str = Field(
        default="Authorization,Content-Type,X-Request-ID", validation_alias="HTTP_CORS_HEADERS"
    )
    max_age: int = Field(default=600, validation_alias="HTTP_CORS_MAX_AGE")

    @property
    def origin_list(self) -> list[str]:
        return _split(self.origins)

    @property
    def method_list(self) -> list[str]:
        return _split(self.methods)

    @property
    def header_list(self) -> list[str]:
        return _split(self.headers)


def apply_cors(app: FastAPI, cfg: CORSConfig) -> None:
    origins = cfg.origin_list
    if not origins:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=cfg.method_list,
        allow_headers=cfg.header_list,
        max_age=cfg.max_age,
    )
