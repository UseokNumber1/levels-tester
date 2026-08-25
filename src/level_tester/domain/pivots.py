from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from level_tester.domain.models import Candle, Pivot, PivotKind


@dataclass(frozen=True, slots=True)
class PivotDetectorConfig:
    wing: int = 2
    min_volume_ratio: Decimal | None = None

    def __post_init__(self) -> None:
        if self.wing < 1:
            raise ValueError("pivot wing must be at least 1")
        if self.min_volume_ratio is not None and self.min_volume_ratio <= 0:
            raise ValueError("min_volume_ratio must be positive")


class CausalPivotDetector:
    """Detects a pivot only when all candles in its right wing are closed."""

    def __init__(self, config: PivotDetectorConfig | None = None) -> None:
        self.config = config or PivotDetectorConfig()
        self._candles: list[Candle] = []
        self._emitted_indices: set[int] = set()

    @property
    def candles(self) -> tuple[Candle, ...]:
        return tuple(self._candles)

    def update(self, candle: Candle) -> list[Pivot]:
        if self._candles and candle.open_time <= self._candles[-1].open_time:
            raise ValueError("candles must be supplied in strictly chronological order")
        self._candles.append(candle)
        candidate = len(self._candles) - self.config.wing - 1
        if candidate < self.config.wing or candidate in self._emitted_indices:
            return []
        self._emitted_indices.add(candidate)
        return self._detect(candidate)

    def _detect(self, index: int) -> list[Pivot]:
        wing = self.config.wing
        center = self._candles[index]
        left = self._candles[index - wing : index]
        right = self._candles[index + 1 : index + wing + 1]
        if self.config.min_volume_ratio is not None:
            neighbours = left + right
            average = sum((bar.volume for bar in neighbours), Decimal("0")) / len(neighbours)
            if average and center.volume < average * self.config.min_volume_ratio:
                return []

        result: list[Pivot] = []
        # Equal highs/lows are rejected on the left and accepted on the right:
        # this consistently keeps the first occurrence in a flat extreme.
        if center.high > max(bar.high for bar in left) and center.high >= max(
            bar.high for bar in right
        ):
            result.append(
                Pivot(
                    id=f"high-{index}",
                    kind=PivotKind.HIGH,
                    price=center.high,
                    pivot_time=center.close_time,
                    confirmed_time=self._candles[index + wing].close_time,
                    source_index=index,
                )
            )
        if center.low < min(bar.low for bar in left) and center.low <= min(
            bar.low for bar in right
        ):
            result.append(
                Pivot(
                    id=f"low-{index}",
                    kind=PivotKind.LOW,
                    price=center.low,
                    pivot_time=center.close_time,
                    confirmed_time=self._candles[index + wing].close_time,
                    source_index=index,
                )
            )
        return result
