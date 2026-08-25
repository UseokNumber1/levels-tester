from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings; secrets come from environment, not from YAML or source."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = Field(default="development", validation_alias="APP_ENV")
    app_host: str = Field(default="127.0.0.1", validation_alias="APP_HOST")
    app_port: int = Field(default=8000, validation_alias="APP_PORT")
    database_url: str = Field(
        default="mysql+pymysql://level_tester:change-me@127.0.0.1:3306/level_tester",
        validation_alias="DATABASE_URL",
    )
    binance_futures_base_url: str = Field(
        default="https://fapi.binance.com",
        validation_alias="BINANCE_FUTURES_BASE_URL",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
