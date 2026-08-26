from __future__ import annotations

from dataclasses import dataclass, field

from level_tester.domain.confirmation import ConfirmationConfig
from level_tester.domain.evaluation import OutcomeProfile
from level_tester.domain.execution import ExecutionConfig
from level_tester.domain.search import LevelConfig, PivotDetectorConfig


@dataclass(frozen=True, slots=True)
class MarketConfig:
    """Target market; drives the Binance endpoint family and symbol filters."""

    exchange: str = "binance"
    market_type: str = "usdt_m_futures"
    quote_asset: str = "USDT"

    def __post_init__(self) -> None:
        if self.exchange != "binance":
            raise ValueError("only the binance exchange is supported")
        if self.market_type not in {"usdt_m_futures", "coin_m_futures"}:
            raise ValueError("market_type must be usdt_m_futures or coin_m_futures")
        if not self.quote_asset or not self.quote_asset.isascii():
            raise ValueError("quote_asset must be a non-empty ASCII string")


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    """Immutable configuration shared by all stages of one replay."""

    pivot: PivotDetectorConfig = field(default_factory=PivotDetectorConfig)
    level: LevelConfig = field(default_factory=LevelConfig)
    default_speed: float = 1.0
    detail_timeframes: tuple[str, ...] = ("1m", "5m")
    detail_timeframe: str = "1m"
    outcome_profiles: tuple[OutcomeProfile, ...] = ()
    confirmation: ConfirmationConfig = field(default_factory=ConfirmationConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    market: MarketConfig = field(default_factory=MarketConfig)

    def __post_init__(self) -> None:
        if not 0 < self.default_speed <= 100:
            raise ValueError("default_speed must be between 0 and 100")
        if not self.detail_timeframes or not set(self.detail_timeframes) <= {"1m", "5m"}:
            raise ValueError("detail_timeframes must contain 1m or 5m")
        if self.detail_timeframe not in self.detail_timeframes:
            raise ValueError("detail_timeframe must be 1m or 5m")
