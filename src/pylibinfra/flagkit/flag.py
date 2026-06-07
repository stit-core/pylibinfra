"""flagkit — простейшие feature flags из ENV.

`FEATURE_FLAGS=foo=true,bar=baz` парсится в Store, который отдаёт bool/str.
Без remote-источников и динамического refresh — для случаев, когда достаточно
kill-switch'а на рестарт.
"""

from typing import Protocol

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    definitions: str = Field(default="", validation_alias="FEATURE_FLAGS")


class Store(Protocol):
    def bool(self, name: str) -> bool: ...
    def str(self, name: str) -> str: ...


class EnvStore:
    def __init__(self, cfg: Config) -> None:
        self._flags: dict[str, str] = {}
        if not cfg.definitions:
            return
        for raw in cfg.definitions.split(","):
            pair = raw.strip()
            key, sep, val = pair.partition("=")
            key = key.strip()
            if sep and key:
                self._flags[key] = val.strip()

    def bool(self, name: str) -> bool:
        v = self._flags.get(name)
        if v is None:
            return False
        return v.lower() == "true" or v == "1"

    def str(self, name: str) -> str:
        return self._flags.get(name, "")
