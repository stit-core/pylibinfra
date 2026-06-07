from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# service.name/version/environment не дублируются как record attrs — они уже в
# OTel resource attrs (telemetry.init). ServiceConfig остаётся для test-сценариев
# и не-OTel вывода, но модулем как record-attrs не инжектится.
class ServiceConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    service_name: str = Field(default="", validation_alias="OTEL_SERVICE_NAME")
    service_version: str = Field(default="dev", validation_alias="OTEL_SERVICE_VERSION")
    environment: str = Field(default="dev", validation_alias="APP_ENV")
