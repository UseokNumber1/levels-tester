from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ReplayStatus(StrEnum):
    CREATED = "created"
    LOADING = "loading"
    READY = "ready"
    PLAYING = "playing"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ReplayWindow:
    """Visible period and its causal warm-up period are intentionally separate."""

    display_from: datetime
    effective_to: datetime
    calculation_from: datetime

    def __post_init__(self) -> None:
        if self.display_from.tzinfo is None or self.effective_to.tzinfo is None:
            raise ValueError("ReplayWindow datetimes must be timezone-aware")
        if self.calculation_from > self.display_from:
            raise ValueError("calculation_from must not be after display_from")
        if self.display_from > self.effective_to:
            raise ValueError("display_from must not be after effective_to")


@dataclass(frozen=True, slots=True)
class ReplayCursor:
    """Position in historical data; it is never based on wall-clock time."""

    bar_time: datetime
    sequence: int

    def __post_init__(self) -> None:
        if self.bar_time.tzinfo is None:
            raise ValueError("ReplayCursor.bar_time must be timezone-aware")
        if self.sequence < 0:
            raise ValueError("ReplayCursor.sequence must be non-negative")
