from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from statistics import median
from uuid import uuid5, NAMESPACE_URL

from level_tester.domain.models import Candle, Level, LevelEvent, LevelSide, LevelState, Pivot


@dataclass(frozen=True, slots=True)
class LevelConfig:
    zone_percent: Decimal = Decimal("0.008")
    min_bounce_percent: Decimal = Decimal("0.045")
    min_touches: int = 2
    breakout: str = "close"
    max_lifetime_bars: int | None = None
    tick_size: Decimal = Decimal("0.01")

    def __post_init__(self) -> None:
        if self.zone_percent <= 0 or self.tick_size <= 0:
            raise ValueError("zone_percent and tick_size must be positive")
        if self.breakout not in {"wick", "close", "both"}:
            raise ValueError("breakout must be wick, close or both")
        if self.min_touches < 1:
            raise ValueError("min_touches must be at least 1")


def quantize_tick(value: Decimal, tick_size: Decimal) -> Decimal:
    units = (value / tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return units * tick_size


class LevelBook:
    def __init__(self, run_id: str, config: LevelConfig | None = None) -> None:
        self.run_id = run_id
        self.config = config or LevelConfig()
        self.levels: list[Level] = []
        self._candles: list[Candle] = []
        self._candle_by_index: dict[int, Candle] = {}
        self._approaching: set[str] = set()
        self._touching: set[str] = set()

    def set_candles(self, candles: list[Candle]) -> None:
        self._candles = sorted(candles, key=lambda c: c.open_time)
        self._candle_by_index = {i: c for i, c in enumerate(self._candles)}

    def add_pivot(self, pivot: Pivot) -> tuple[Level, bool]:
        side = LevelSide.RESISTANCE if pivot.kind.value == "high" else LevelSide.SUPPORT

        for level in self.levels:
            if level.side != side:
                continue
            if level.state in {LevelState.BROKEN, LevelState.EXPIRED}:
                continue
            if not (level.zone_low <= pivot.price <= level.zone_high):
                continue
            if not self._has_rebound(level, pivot):
                continue
            level.source_pivot_ids.append(pivot.id)
            level.touch_count += 1
            level.last_touch_time = pivot.confirmed_time
            self._recalculate_zone(level)
            is_new = level.state == LevelState.CREATED
            if level.state == LevelState.CREATED and level.touch_count >= self.config.min_touches:
                level.state = LevelState.CONFIRMED
                level.confirmed_time = pivot.confirmed_time
            return level, is_new

        width = max(pivot.price * self.config.zone_percent, self.config.tick_size)
        initial_state = LevelState.CONFIRMED if self.config.min_touches <= 1 else LevelState.CREATED
        level = Level(
            id=str(uuid5(NAMESPACE_URL, f"{self.run_id}:{pivot.id}")),
            side=side,
            price=quantize_tick(pivot.price, self.config.tick_size),
            zone_low=pivot.price - width,
            zone_high=pivot.price + width,
            created_time=pivot.confirmed_time,
            confirmed_time=pivot.confirmed_time,
            source_pivot_ids=[pivot.id],
            state=initial_state,
            touched_time=None,
            touch_count=1,
            last_touch_time=pivot.confirmed_time,
        )
        self.levels.append(level)
        return level, True

    def _has_rebound(self, level: Level, new_pivot: Pivot) -> bool:
        if self.config.min_bounce_percent <= 0:
            return True
        if len(self._candles) == 0:
            return True
        if not level.source_pivot_ids:
            return True
        last_pivot_id = level.source_pivot_ids[-1]
        last_source_index = self._extract_source_index(last_pivot_id)
        new_source_index = new_pivot.source_index
        if last_source_index is None:
            return True
        start = min(last_source_index, new_source_index) + 1
        end = max(last_source_index, new_source_index)
        if start >= end:
            return False
        if end >= len(self._candles):
            return False
        level_price = level.price
        if level.side == LevelSide.RESISTANCE:
            for i in range(start, end):
                c = self._candle_by_index.get(i)
                if c is None:
                    continue
                rebound = (level_price - c.low) / level_price
                if rebound >= self.config.min_bounce_percent:
                    return True
            return False
        else:
            for i in range(start, end):
                c = self._candle_by_index.get(i)
                if c is None:
                    continue
                rebound = (c.high - level_price) / level_price
                if rebound >= self.config.min_bounce_percent:
                    return True
            return False

    def _extract_source_index(self, pivot_id: str) -> int | None:
        parts = pivot_id.rsplit("-", 1)
        if len(parts) == 2:
            try:
                return int(parts[1])
            except ValueError:
                return None
        return None

    def _recalculate_zone(self, level: Level) -> None:
        pivot_prices = []
        for pid in level.source_pivot_ids:
            idx = self._extract_source_index(pid)
            if idx is not None:
                c = self._candle_by_index.get(idx)
                if c is not None:
                    if level.side == LevelSide.RESISTANCE:
                        pivot_prices.append(c.high)
                    else:
                        pivot_prices.append(c.low)
        if not pivot_prices:
            return
        med = Decimal(str(median([float(p) for p in pivot_prices])))
        level.price = quantize_tick(med, self.config.tick_size)
        width = max(level.price * self.config.zone_percent, self.config.tick_size)
        level.zone_low = level.price - width
        level.zone_high = level.price + width

    def _is_level_breached(self, level: Level) -> bool:
        if not self._candles:
            return False
        for pid in level.source_pivot_ids:
            src_idx = self._extract_source_index(pid)
            if src_idx is None:
                continue
            for i in range(src_idx, len(self._candles)):
                c = self._candle_by_index.get(i)
                if c is None:
                    continue
                if level.side == LevelSide.RESISTANCE:
                    if c.close > level.zone_high:
                        return True
                else:
                    if c.close < level.zone_low:
                        return True
        return False

    def evaluate(self, candle: Candle, sequence: int, level: Level) -> list[LevelEvent]:
        if level.state in {LevelState.BROKEN, LevelState.EXPIRED}:
            return []
        if level.state == LevelState.CREATED:
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

        # Match the scanner: a touch is based only on the relevant wick.
        # A candle crossing the whole zone must not count as a touch.
        relevant_price = (
            candle.high if level.side == LevelSide.RESISTANCE else candle.low
        )
        wick_break = (
            relevant_price > level.zone_high
            if level.side == LevelSide.RESISTANCE
            else relevant_price < level.zone_low
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
            self._approaching.discard(level.id)
            self._touching.discard(level.id)
            level.state = LevelState.BROKEN
            level.broken_time = candle.close_time
            return [
                self._event(
                    sequence, candle, level, "level.broken", f"{self.config.breakout} breakout"
                )
            ]

        if level.side == LevelSide.RESISTANCE:
            touched = level.zone_low <= relevant_price <= level.zone_high
            approaching = level.zone_low - (level.zone_high - level.zone_low) <= relevant_price < level.zone_low
        else:
            touched = level.zone_low <= relevant_price <= level.zone_high
            approaching = level.zone_high < relevant_price <= level.zone_high + (level.zone_high - level.zone_low)

        if approaching and level.id not in self._approaching:
            self._approaching.add(level.id)
            events.append(
                self._event(sequence, candle, level, "level.approaching", "price approaching zone")
            )
        elif not approaching:
            self._approaching.discard(level.id)

        if touched:
            self._approaching.discard(level.id)
            if level.id not in self._touching:
                self._touching.add(level.id)
                level.touch_count += 1
                level.last_touch_time = candle.close_time
                if level.touched_time is None:
                    level.touched_time = candle.close_time
                level.state = LevelState.TOUCHED
                events.append(
                    self._event(sequence, candle, level, "level.touched", "price entered zone")
                )
        else:
            self._touching.discard(level.id)
            if level.state == LevelState.TOUCHED:
                level.state = LevelState.WAITING_TOUCH
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
