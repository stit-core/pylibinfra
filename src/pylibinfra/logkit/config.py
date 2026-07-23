from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    level: str = Field(default="info", validation_alias="LOG_LEVEL")
    # Писать записи в JSON-stdout. False — вывод отключён.
    stdout: bool = Field(default=True, validation_alias="LOG_STDOUT")
