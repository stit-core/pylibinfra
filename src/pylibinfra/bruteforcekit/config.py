from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    max_failures: int = Field(default=5, validation_alias="BRUTEFORCE_MAX_FAILURES")
    # секунды (Go time.Duration → float-секунды): дефолты 15m / 1h / 24h.
    lock_duration: float = Field(default=900.0, validation_alias="BRUTEFORCE_LOCK_DURATION")
    window: float = Field(default=3600.0, validation_alias="BRUTEFORCE_WINDOW")
    cleanup_retention: float = Field(
        default=86400.0, validation_alias="BRUTEFORCE_CLEANUP_RETENTION"
    )
