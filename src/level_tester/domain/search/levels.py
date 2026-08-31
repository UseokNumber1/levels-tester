from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from uuid import NAMESPACE_URL, uuid5

from level_tester.domain.models import Candle, Level, LevelEvent, LevelSide, LevelState, Pivot, PivotKind

NAMESPACE_LEVEL = uuid5(NAMESPACE_URL, "level-tester/level")
ONE = Decimal("1")
NONE = Decimal("0")


@dataclass(frozen=True, slots=True)
class LevelConfig:
    zone_percent: Decimal = Decimal("0.008")
    min_bounce_percent: Decimal = Decimal("0.045")
    min_touches: int = 2
    breakout: str = "close"
    max_lifetime_bars: int | None = None
    tick_size: Decimal = Decimal("0.01")
    max_lookahead: int = 50
    wick_breach_pct: Decimal = Decimal("0.01")
    half_zone_ratio: Decimal = Decimal("0.5")

    def __post_init__(self) -> None:
        if self.zone_percent <= 0 or self.tick_size <= 0:
            raise ValueError("zone_percent and tick_size must be positive")
        if self.breakout not in {"wick", "close", "both"}:
            raise ValueError("breakout must be wick, close or both")
        if self.min_touches < 1:
            raise ValueError("min_touches must be at least 1")


def quantize_tick(value: Decimal, tick_size: Decimal) -> Decimal:
    units = (value / tick_size).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return units * tick_size


def price_precision_from_tick(tick_size: Decimal) -> int:
    """Number of decimal places implied by a tick size (e.g. 0.0001 -> 4)."""
    text = str(tick_size)
    if "." not in text:
        return 0
    fraction = text.split(".", 1)[1]
    return len(fraction.rstrip("0"))


def median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return NONE
    if n % 2 == 1:
        return ordered[n // 2]
    return (ordered[n // 2 - 1] + ordered[n // 2]) / Decimal(2)


class LevelBook:
    """Levels found with the same clustering algorithm as MVP_1H.

    Pivots arrive causally (oldest first); on every new pivot the full level set
    is rebuilt from all pivots seen so far using MVP_1H's batch ``_build_by_type``
    clustering. This keeps behaviour (and the level set) identical to the MVP_1H
    scanner while still emitting causal touch/break events per candle.
    """

    def __init__(self, run_id: str, config: LevelConfig | None = None) -> None:
        self.run_id = run_id
        self.config = config or LevelConfig()
        self.levels: list[Level] = []
        self._pivots: list[Pivot] = []
        self._highs: list[Decimal] = []
        self._lows: list[Decimal] = []
        self._closes: list[Decimal] = []
        self._approaching: set[str] = set()
        self._touching: set[str] = set()
        self._broken_ids: set[str] = set()

    def set_candles(self, candles: list[Candle]) -> None:
        ordered = sorted(candles, key=lambda c: c.open_time)
        self._highs = [c.high for c in ordered]
        self._lows = [c.low for c in ordered]
        self._closes = [c.close for c in ordered]
        self._rebuild()

    def add_pivot(self, pivot: Pivot) -> tuple[Level | None, bool]:
        self._pivots.append(pivot)
        previous_ids = {lv.id for lv in self.levels}
        self._rebuild()
        level = next(
            (lv for lv in self.levels if lv.source_pivot_ids and lv.source_pivot_ids[0] == pivot.id),
            None,
        )
        created = level is not None and level.id not in previous_ids
        return level, created

    # -- clustering (ported from MVP_1H/core/levels.py) ------------------------

    def _rebuild(self) -> None:
        if not self._highs:
            self.levels = []
            return
        levels: list[Level] = []
        levels += self._build_by_type(PivotKind.HIGH, "resistance")
        levels += self._build_by_type(PivotKind.LOW, "support")
        result: list[Level] = []
        kept = {lv.id for lv in levels}
        for lv in levels:
            if lv.id in self._broken_ids:
                lv.state = LevelState.BROKEN
            result.append(lv)
        for old in self.levels:
            if old.id in self._broken_ids and old.id not in kept:
                result.append(old)
        self.levels = result

    def _build_by_type(self, kind: "PivotKind", side: str) -> list[Level]:
        cfg = self.config
        zp = cfg.zone_percent
        mrb = cfg.min_bounce_percent
        ml = cfg.max_lookahead
        items = sorted(
            (p for p in self._pivots if p.kind == kind),
            key=lambda p: p.source_index,
            reverse=True,
        )
        levels: list[Level] = []
        used: set[int] = set()
        i = 0
        while i < len(items):
            if i in used:
                i += 1
                continue
            pivot = items[i]
            if not self._has_rebound_after_first_pivot(pivot.source_index, float(pivot.price), side, mrb, ml):
                i += 1
                continue
            zone_top = pivot.price * (ONE + zp)
            zone_bottom = pivot.price * (ONE - zp)
            cluster = [pivot]
            last = pivot
            for p in items[i + 1 :]:
                if p.source_index in used:
                    continue
                if p.source_index < last.source_index:
                    ok_direction = last.price > p.price if side == "support" else last.price < p.price
                    if not ok_direction:
                        break
                    within_zone = p.price > zone_bottom if side == "support" else p.price < zone_top
                    if not within_zone:
                        break
                    if last is not pivot:
                        if not self._has_rebound(last.source_index, p.source_index, float(last.price), side, mrb):
                            continue
                    cluster.append(p)
                    last = p
                else:
                    break
            levels.append(self._make_level(cluster, side))
            used.update(p.source_index for p in cluster)
            i += 1
        return levels

    def _has_rebound(
        self,
        i1: int,
        i2: int,
        level_price: float,
        side: str,
        min_rebound: Decimal,
    ) -> bool:
        if min_rebound <= 0:
            return True
        start = min(i1, i2) + 1
        end = max(i1, i2)
        if start >= end or end >= len(self._highs):
            return False
        threshold = Decimal(str(level_price)) * (ONE - min_rebound if side == "support" else ONE + min_rebound)
        if side == "support":
            return any(self._lows[j] <= threshold for j in range(start, end))
        return any(self._highs[j] >= threshold for j in range(start, end))

    def _has_rebound_after_first_pivot(
        self,
        idx: int,
        level_price: float,
        side: str,
        min_rebound: Decimal,
        max_lookahead: int,
    ) -> bool:
        if min_rebound <= 0:
            return True
        start = idx + 1
        end = min(start + max_lookahead, len(self._highs))
        if start >= end:
            return False
        threshold = Decimal(str(level_price)) * (ONE - min_rebound if side == "support" else ONE + min_rebound)
        if side == "support":
            return any(self._lows[j] <= threshold for j in range(start, end))
        return any(self._highs[j] >= threshold for j in range(start, end))

    def _make_level(self, cluster: list[Pivot], side: str) -> Level:
        anchor = cluster[0]
        price = quantize_tick(median([p.price for p in cluster]), self.config.tick_size)
        zone_low = quantize_tick(price * (ONE - self.config.zone_percent), self.config.tick_size)
        zone_high = quantize_tick(price * (ONE + self.config.zone_percent), self.config.tick_size)
        touch_count = len(cluster)
        state = LevelState.CONFIRMED if touch_count >= self.config.min_touches else LevelState.CREATED
        confirmed_time = min(p.confirmed_time for p in cluster)
        level_id = uuid5(NAMESPACE_LEVEL, f"{self.run_id}:{anchor.id}").hex
        return Level(
            id=level_id,
            side=LevelSide.RESISTANCE if side == "resistance" else LevelSide.SUPPORT,
            price=price,
            zone_low=zone_low,
            zone_high=zone_high,
            created_time=confirmed_time,
            confirmed_time=confirmed_time,
            source_pivot_ids=[p.id for p in cluster],
            state=state,
            touched_time=None,
            broken_time=None,
            expired_time=None,
            last_touch_time=None,
            touch_count=touch_count,
        )

    # -- per-candle touch / break detection (matches the scanner's wick model) -

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

        relevant_price = candle.high if level.side == LevelSide.RESISTANCE else candle.low
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
            self._broken_ids.add(level.id)
            return [
                self._event(
                    sequence, candle, level, "level.broken", f"{self.config.breakout} breakout"
                )
            ]

        if level.side == LevelSide.RESISTANCE:
            touched = relevant_price >= level.price
            band = level.zone_high - level.zone_low
            approaching = level.zone_low - band <= relevant_price < level.zone_low
        else:
            touched = relevant_price <= level.price
            band = level.zone_high - level.zone_low
            approaching = level.zone_high < relevant_price <= level.zone_high + band

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
            payload={
                "state": level.state.value,
                "price": str(level.price),
                "candle_open_time": candle.open_time.isoformat(),
                "candle_close_time": candle.close_time.isoformat(),
            },
        )
