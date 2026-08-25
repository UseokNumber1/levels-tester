from functools import lru_cache
from decimal import Decimal
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
import yaml

from level_tester.domain.levels import LevelConfig
from level_tester.domain.outcomes import OutcomeProfile
from level_tester.domain.pivots import PivotDetectorConfig
from level_tester.domain.replay import ReplayConfig


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
        default="sqlite:///./levels_tester.db",
        validation_alias="DATABASE_URL",
    )
    binance_futures_base_url: str = Field(
        default="https://fapi.binance.com",
        validation_alias="BINANCE_FUTURES_BASE_URL",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_replay_config(path: str | Path = "config/default.yaml") -> ReplayConfig:
    """Load non-secret algorithm parameters; secrets never come from YAML."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    algorithm = raw.get("algorithm", {})
    pivot = algorithm.get("pivot", {})
    level = algorithm.get("level", {})
    profiles = tuple(
        OutcomeProfile(
            item["name"],
            int(item.get("wait_bars", 3)),
            Decimal(str(item.get("min_bounce_percent", "0"))),
        )
        for item in algorithm.get("outcomes", [])
    )
    return ReplayConfig(
        pivot=PivotDetectorConfig(
            wing=int(pivot.get("wing", 2)),
            min_volume_ratio=Decimal(str(pivot["min_volume_ratio"]))
            if pivot.get("min_volume_ratio")
            else None,
        ),
        level=LevelConfig(
            zone_percent=Decimal(str(level.get("zone_percent", "0.001"))),
            min_bounce_percent=Decimal(str(level.get("min_bounce_percent", "0"))),
            breakout=level.get("breakout", "close"),
            max_lifetime_bars=level.get("max_lifetime_bars"),
            tick_size=Decimal(str(level.get("tick_size", "0.01"))),
        ),
        detail_timeframe=raw.get("replay", {}).get("detail_timeframes", ["1m"])[0],
        outcome_profiles=profiles,
    )
