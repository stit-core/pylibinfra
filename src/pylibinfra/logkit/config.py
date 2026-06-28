from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    level: str = Field(default="info", validation_alias="LOG_LEVEL")
    # Дублировать записи в JSON-stdout (вдобавок к OTLP). False — только OTLP.
    stdout: bool = Field(default=True, validation_alias="LOG_STDOUT")
