from __future__ import annotations

from dataclasses import dataclass, field

from level_tester.domain.confirmation import ConfirmationConfig
from level_tester.domain.evaluation import OutcomeProfile
from level_tester.domain.execution import ExecutionConfig
from level_tester.domain.search import LevelConfig, PivotDetectorConfig


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    """Immutable configuration shared by all stages of one replay."""

    pivot: PivotDetectorConfig = field(default_factory=PivotDetectorConfig)
    level: LevelConfig = field(default_factory=LevelConfig)
    detail_timeframe: str = "1m"
    outcome_profiles: tuple[OutcomeProfile, ...] = ()
    confirmation: ConfirmationConfig = field(default_factory=ConfirmationConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)

    def __post_init__(self) -> None:
        if self.detail_timeframe not in {"1m", "5m"}:
            raise ValueError("detail_timeframe must be 1m or 5m")
