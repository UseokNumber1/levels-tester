from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from level_tester.domain.configuration import MarketConfig, StrategyConfig
from level_tester.domain.confirmation import ConfirmationConfig
from level_tester.domain.evaluation import OutcomeProfile
from level_tester.domain.execution import ExecutionConfig
from level_tester.domain.search import LevelConfig, PivotDetectorConfig


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
    app_port: int = Field(default=8080, validation_alias="APP_PORT")
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


def load_replay_config(path: str | Path = "config/default.yaml") -> StrategyConfig:
    """Load non-secret algorithm parameters; secrets never come from YAML."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    algorithm = raw.get("algorithm", {})
    replay = raw.get("replay", {})
    pivot = algorithm.get("pivot", {})
    level = algorithm.get("level", {})
    confirmation = algorithm.get("confirmation", {})
    execution = algorithm.get("execution", {})
    market = raw.get("market", {})
    detail_timeframes = tuple(replay.get("detail_timeframes", ["1m", "5m"]))
    profiles = tuple(
        OutcomeProfile(
            item["name"],
            int(item.get("wait_bars", 3)),
            Decimal(str(item.get("min_bounce_percent", "0"))),
        )
        for item in algorithm.get("outcomes", [])
    )
    return StrategyConfig(
        pivot=PivotDetectorConfig(
            wing=int(pivot.get("wing", 2)),
            min_volume_ratio=Decimal(str(pivot["min_volume_ratio"]))
            if pivot.get("min_volume_ratio")
            else None,
        ),
        level=LevelConfig(
            zone_percent=Decimal(str(level.get("zone_percent", "0.008"))),
            min_bounce_percent=Decimal(str(level.get("min_bounce_percent", "0.045"))),
            min_touches=int(level.get("min_touches", 2)),
            breakout=level.get("breakout", "close"),
            max_lifetime_bars=level.get("max_lifetime_bars"),
            tick_size=Decimal(str(level.get("tick_size", "0.01"))),
        ),
        default_speed=float(replay.get("default_speed", 1.0)),
        detail_timeframes=detail_timeframes,
        detail_timeframe=detail_timeframes[0],
        outcome_profiles=profiles,
        confirmation=ConfirmationConfig(
            timeframe=confirmation.get("timeframe", "5m"),
            required_bars=int(confirmation.get("required_bars", 2)),
            max_wait_bars=int(confirmation.get("max_wait_bars", 15)),
            entry_on_next_bar=bool(confirmation.get("entry_on_next_bar", True)),
        ),
        execution=ExecutionConfig(
            stop_buffer_percent=Decimal(str(execution.get("stop_buffer_percent", "0.002"))),
            risk_reward=Decimal(str(execution.get("risk_reward", "2"))),
            fee_percent=Decimal(str(execution.get("fee_percent", "0"))),
            slippage_percent=Decimal(str(execution.get("slippage_percent", "0"))),
        ),
        market=MarketConfig(
            exchange=market.get("exchange", "binance"),
            market_type=market.get("market_type", "usdt_m_futures"),
            quote_asset=market.get("quote_asset", "USDT"),
        ),
    )
