from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from itertools import pairwise

from level_tester.domain.models import Candle


@dataclass(frozen=True, slots=True)
class DataQualityReport:
    candle_count: int
    duplicate_count: int
    gap_count: int
    invalid_count: int

    @property
    def is_valid(self) -> bool:
        return self.duplicate_count == 0 and self.gap_count == 0 and self.invalid_count == 0


def inspect_candles(candles: list[Candle], expected_step: timedelta) -> DataQualityReport:
    """Check data without filling gaps: missing exchange data must remain visible."""
    ordered = sorted(candles, key=lambda candle: candle.open_time)
    duplicate_count = sum(
        left.open_time == right.open_time for left, right in pairwise(ordered)
    )
    gap_count = sum(
        right.open_time - left.open_time != expected_step for left, right in pairwise(ordered)
    )
    invalid_count = sum(
        candle.low > min(candle.open, candle.close)
        or candle.high < max(candle.open, candle.close)
        or candle.close_time <= candle.open_time
        for candle in candles
    )
    return DataQualityReport(len(candles), duplicate_count, gap_count, invalid_count)
