from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    endpoint: str = Field(default="", validation_alias="STORAGE_S3_ENDPOINT")
    access_key: str = Field(default="", validation_alias="STORAGE_S3_ACCESS_KEY")
    secret_key: str = Field(default="", validation_alias="STORAGE_S3_SECRET_KEY")
    bucket: str = Field(default="", validation_alias="STORAGE_S3_BUCKET")
    use_ssl: bool = Field(default=False, validation_alias="STORAGE_S3_USE_SSL")
    public_base_url: str = Field(default="", validation_alias="STORAGE_PUBLIC_BASE_URL")
    region: str = Field(default="us-east-1", validation_alias="STORAGE_S3_REGION")
