from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    service_name: str = Field(validation_alias="OTEL_SERVICE_NAME")
    service_version: str = Field(default="dev", validation_alias="OTEL_SERVICE_VERSION")
    environment: str = Field(default="dev", validation_alias="APP_ENV")
    otlp_endpoint: str = Field(
        default="localhost:4318", validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    otlp_insecure: bool = Field(default=True, validation_alias="OTEL_EXPORTER_OTLP_INSECURE")
    sample_ratio: float = Field(default=1.0, validation_alias="OTEL_TRACES_SAMPLER_ARG")
    # секунды (batch timeout трейсов, интервал экспорта метрик)
    export_timeout: float = Field(default=10.0, validation_alias="OTEL_EXPORT_TIMEOUT")

    # Tenant идентифицирует проект в корп observability-стеке: экспортируется как
    # resource-атрибут service.namespace и свободный атрибут tenant. Пусто — для
    # local-dev или неаутентифицированного коллектора (в проде force-сетит
    # корп otel-collector из X-Scope-OrgID).
    tenant: str = Field(default="", validation_alias="OTEL_TENANT")

    # Если задан — добавляется как `Authorization: Bearer ...` к OTLP-запросам.
    otlp_bearer_token: str = Field(default="", validation_alias="OTEL_TOKEN")

    stdout_only: bool = Field(default=False, validation_alias="OTEL_STDOUT_ONLY")
