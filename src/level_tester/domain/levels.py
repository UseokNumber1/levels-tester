from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid5, NAMESPACE_URL

from level_tester.domain.models import Candle, Level, LevelEvent, LevelSide, LevelState, Pivot


@dataclass(frozen=True, slots=True)
class LevelConfig:
    zone_percent: Decimal = Decimal("0.001")
    min_bounce_percent: Decimal = Decimal("0")
    breakout: str = "close"
    max_lifetime_bars: int | None = None
    tick_size: Decimal = Decimal("0.01")

    def __post_init__(self) -> None:
        if self.zone_percent <= 0 or self.tick_size <= 0:
            raise ValueError("zone_percent and tick_size must be positive")
        if self.breakout not in {"wick", "close", "both"}:
            raise ValueError("breakout must be wick, close or both")


def quantize_tick(value: Decimal, tick_size: Decimal) -> Decimal:
    units = (value / tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return units * tick_size


class LevelBook:
    def __init__(self, run_id: str, config: LevelConfig | None = None) -> None:
        self.run_id = run_id
        self.config = config or LevelConfig()
        self.levels: list[Level] = []

    def add_pivot(self, pivot: Pivot) -> tuple[Level, bool]:
        side = LevelSide.RESISTANCE if pivot.kind.value == "high" else LevelSide.SUPPORT
        width = max(pivot.price * self.config.zone_percent, self.config.tick_size)
        for level in self.levels:
            if level.side == side and abs(level.price - pivot.price) <= max(
                level.zone_high - level.zone_low, width
            ):
                level.source_pivot_ids.append(pivot.id)
                level.zone_low = min(level.zone_low, pivot.price - width)
                level.zone_high = max(level.zone_high, pivot.price + width)
                level.price = quantize_tick(
                    (level.zone_low + level.zone_high) / Decimal("2"), self.config.tick_size
                )
                return level, False
        level = Level(
            id=str(uuid5(NAMESPACE_URL, f"{self.run_id}:{pivot.id}")),
            side=side,
            price=quantize_tick(pivot.price, self.config.tick_size),
            zone_low=pivot.price - width,
            zone_high=pivot.price + width,
            created_time=pivot.confirmed_time,
            confirmed_time=pivot.confirmed_time,
            source_pivot_ids=[pivot.id],
            state=LevelState.CONFIRMED,
        )
        self.levels.append(level)
        return level, True

    def evaluate(self, candle: Candle, sequence: int, level: Level) -> list[LevelEvent]:
        if level.state in {LevelState.BROKEN, LevelState.EXPIRED}:
            return []
        events: list[LevelEvent] = []
        if self.config.max_lifetime_bars is not None:
            age = (candle.open_time - level.confirmed_time).total_seconds() / 3600
            if age >= self.config.max_lifetime_bars:
                level.state = LevelState.EXPIRED
                level.expired_time = candle.close_time
                return [self._event(sequence, candle, level, "level.expired", "lifetime exceeded")]

        if level.confirmed_time >= candle.close_time:
            return []
        if level.state == LevelState.CONFIRMED:
            level.state = LevelState.WAITING_TOUCH

        wick_break = (
            candle.high > level.zone_high
            if level.side == LevelSide.RESISTANCE
            else candle.low < level.zone_low
        )
        close_break = (
            candle.close > level.zone_high
            if level.side == LevelSide.RESISTANCE
            else candle.close < level.zone_low
        )
        broken = {"wick": wick_break, "close": close_break, "both": wick_break and close_break}[
            self.config.breakout
        ]
        if broken:
            level.state = LevelState.BROKEN
            level.broken_time = candle.close_time
            return [
                self._event(
                    sequence, candle, level, "level.broken", f"{self.config.breakout} breakout"
                )
            ]

        touched = candle.low <= level.zone_high and candle.high >= level.zone_low
        if touched:
            level.touch_count += 1
            level.last_touch_time = candle.close_time
            if level.touched_time is None:
                level.touched_time = candle.close_time
            level.state = LevelState.TOUCHED
            events.append(
                self._event(sequence, candle, level, "level.touched", "price entered zone")
            )
        return events

    def _event(
        self, sequence: int, candle: Candle, level: Level, event_type: str, reason: str
    ) -> LevelEvent:
        return LevelEvent(
            run_id=self.run_id,
            sequence=sequence,
            event_time=candle.close_time,
            event_type=event_type,
            level_id=level.id,
            reason=reason,
            payload={"state": level.state.value, "price": str(level.price)},
        )
