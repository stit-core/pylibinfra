from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# service.name/version/environment — идентификация сервиса для test-сценариев;
# модулем как record-attrs не инжектится.
class ServiceConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    service_name: str = Field(default="", validation_alias="OTEL_SERVICE_NAME")
    service_version: str = Field(default="dev", validation_alias="OTEL_SERVICE_VERSION")
    environment: str = Field(default="dev", validation_alias="APP_ENV")
