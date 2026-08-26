from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from itertools import pairwise
from typing import Any

from level_tester.domain.configuration import StrategyConfig
from level_tester.domain.confirmation import EntryConfirmation
from level_tester.domain.evaluation import OutcomeEvaluator
from level_tester.domain.execution import TradeExecution
from level_tester.domain.models import Candle, LevelEvent, LevelState, Pivot, as_json
from level_tester.domain.search import CausalPivotDetector, LevelBook


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
        if any(
            value.tzinfo is None or value.utcoffset() is None
            for value in (self.display_from, self.effective_to, self.calculation_from)
        ):
            raise ValueError("ReplayWindow datetimes must be timezone-aware")
        object.__setattr__(self, "display_from", self.display_from.astimezone(UTC))
        object.__setattr__(self, "effective_to", self.effective_to.astimezone(UTC))
        object.__setattr__(self, "calculation_from", self.calculation_from.astimezone(UTC))
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
        if self.bar_time.tzinfo is None or self.bar_time.utcoffset() is None:
            raise ValueError("ReplayCursor.bar_time must be timezone-aware")
        if self.sequence < 0:
            raise ValueError("ReplayCursor.sequence must be non-negative")
        object.__setattr__(self, "bar_time", self.bar_time.astimezone(UTC))


ReplayConfig = StrategyConfig


class ReplayEngine:
    """Single canonical, step-based source of historical time for all consumers."""

    def __init__(
        self,
        run_id: str,
        window: ReplayWindow,
        master_candles: list[Candle],
        detail_candles: list[Candle] | None = None,
        config: ReplayConfig | None = None,
    ) -> None:
        self.run_id = run_id
        self.window = window
        self.config = config or ReplayConfig()
        self._master_candles = self._validate_timeline(master_candles, "1h")
        self._detail_candles = self._validate_timeline(
            detail_candles or [], self.config.detail_timeframe
        )
        self._source_candles = [
            candle
            for candle in self._master_candles
            if candle.open_time >= window.calculation_from
            and candle.close_time <= window.effective_to
        ]
        if not self._source_candles:
            raise ValueError("master timeline has no candles in the calculation window")
        self.reset()

    @staticmethod
    def _validate_timeline(candles: list[Candle], timeframe: str) -> list[Candle]:
        ordered = sorted(candles, key=lambda candle: candle.open_time)
        if any(candle.timeframe != timeframe for candle in ordered):
            raise ValueError(f"timeline contains a candle with timeframe other than {timeframe}")
        if any(left.open_time == right.open_time for left, right in pairwise(ordered)):
            raise ValueError("timeline contains duplicate candle open_time values")
        return ordered

    @property
    def status(self) -> ReplayStatus:
        return self._status

    @property
    def cursor(self) -> ReplayCursor | None:
        return self._cursor

    @property
    def events(self) -> tuple[LevelEvent, ...]:
        return tuple(self._events)

    @property
    def pivots(self) -> tuple[Pivot, ...]:
        return tuple(self._pivots)

    def reset(self) -> None:
        self._detector = CausalPivotDetector(self.config.pivot)
        self._levels = LevelBook(self.run_id, self.config.level)
        self._levels.set_candles(list(self._master_candles))
        self._confirmation = EntryConfirmation(self.run_id, self.config.confirmation)
        self._execution = TradeExecution(self.run_id, self.config.execution)
        self._outcomes = OutcomeEvaluator(self.run_id, self.config.outcome_profiles)
        self._pivots: list[Pivot] = []
        self._events: list[LevelEvent] = []
        self._cursor: ReplayCursor | None = None
        self._next_index = 0
        self._status = ReplayStatus.READY

    def set_detail_candles(self, candles: list[Candle]) -> None:
        combined = {
            (detail.timeframe, detail.open_time): detail
            for detail in self._detail_candles
        }
        combined.update({(detail.timeframe, detail.open_time): detail for detail in candles})
        self._detail_candles = self._validate_timeline(
            list(combined.values()), self.config.detail_timeframe
        )
        if self._cursor is not None:
            visible_detail = [
                detail
                for detail in self._detail_candles
                if detail.close_time <= self._cursor.bar_time
            ]
            historical_touches = [
                event
                for event in self._events
                if event.event_type == "level.touched"
                and event.sequence <= self._cursor.sequence
            ]
            pipeline_events = self._evaluate_trade_pipeline(
                visible_detail, historical_touches, self._cursor.sequence
            )
            self._events.extend(pipeline_events)

    @property
    def detail_candles(self) -> tuple[Candle, ...]:
        return tuple(self._detail_candles)

    def pause(self) -> None:
        if self._status == ReplayStatus.PLAYING:
            self._status = ReplayStatus.PAUSED
        elif self._status not in {ReplayStatus.PAUSED, ReplayStatus.READY}:
            raise ValueError(f"cannot pause replay in {self._status.value} state")

    def cancel(self) -> None:
        if self._status not in {ReplayStatus.COMPLETED, ReplayStatus.FAILED}:
            self._status = ReplayStatus.CANCELLED

    def step(self) -> dict[str, Any]:
        if self._status in {ReplayStatus.COMPLETED, ReplayStatus.CANCELLED}:
            return self.snapshot()
        if self._next_index >= len(self._source_candles):
            self._status = ReplayStatus.COMPLETED
            return self.snapshot()
        self._status = ReplayStatus.PLAYING
        candle = self._source_candles[self._next_index]
        self._next_index += 1
        sequence = self._next_index
        self._cursor = ReplayCursor(candle.close_time, sequence)
        new_pivots = self._detector.update(candle)
        emitted: list[LevelEvent] = []
        for pivot in new_pivots:
            self._pivots.append(pivot)
            previous_states = {item.id: item.state for item in self._levels.levels}
            level, created = self._levels.add_pivot(pivot)
            emitted.append(
                self._event(
                    sequence,
                    pivot.confirmed_time,
                    "pivot.confirmed",
                    level.id,
                    {
                        "pivot_id": pivot.id,
                        "kind": pivot.kind.value,
                        "price": str(pivot.price),
                        "pivot_time": pivot.pivot_time.isoformat(),
                    },
                )
            )
            if created:
                emitted.append(
                    self._event(
                        sequence,
                        pivot.confirmed_time,
                        "level.created",
                        level.id,
                        {
                            "side": level.side.value,
                            "price": str(level.price),
                            "zone_low": str(level.zone_low),
                            "zone_high": str(level.zone_high),
                        },
                    )
                )
            if previous_states.get(level.id) == LevelState.CREATED and level.state == LevelState.CONFIRMED:
                emitted.append(
                    self._event(
                        sequence,
                        pivot.confirmed_time,
                        "level.confirmed",
                        level.id,
                        {
                            "side": level.side.value,
                            "price": str(level.price),
                            "zone_low": str(level.zone_low),
                            "zone_high": str(level.zone_high),
                        },
                    )
                )
        for level in self._levels.levels:
            emitted.extend(self._levels.evaluate(candle, sequence, level))
        visible_detail = [
            detail
            for detail in self._detail_candles
            if detail.close_time <= candle.close_time
        ]
        emitted.extend(self._evaluate_trade_pipeline(visible_detail, emitted, sequence))
        emitted.extend(self._outcomes.evaluate(candle, self._levels.levels, emitted, sequence))
        self._events.extend(emitted)
        if self._next_index == len(self._source_candles):
            self._status = ReplayStatus.COMPLETED
        else:
            self._status = ReplayStatus.PAUSED
        return self.snapshot(emitted)

    def _evaluate_trade_pipeline(
        self,
        detail_candles: list[Candle],
        events: list[LevelEvent],
        sequence: int,
    ) -> list[LevelEvent]:
        setup_statuses = {setup.id: setup.status for setup in self._confirmation.setups}
        trade_statuses = {trade.id: trade.status for trade in self._execution.trades}
        self._confirmation.evaluate(
            detail_candles, self._levels.levels, events, sequence
        )
        self._execution.evaluate(
            self._confirmation.setups, self._levels.levels, detail_candles
        )
        emitted: list[LevelEvent] = []
        for setup in self._confirmation.setups:
            if (
                setup.status.value == "entry_confirmed"
                and setup_statuses.get(setup.id) != setup.status
            ):
                emitted.append(
                    self._event(
                        sequence,
                        setup.confirmed_time or setup.touch_time,
                        "entry.confirmed",
                        setup.level_id,
                        {"setup_id": setup.id, "entry_time": setup.entry_time},
                    )
                )
        for trade in self._execution.trades:
            if trade.id not in trade_statuses:
                emitted.append(
                    self._event(
                        sequence,
                        trade.entry_time,
                        "trade.opened",
                        trade.setup_id,
                        {
                            "trade_id": trade.id,
                            "entry_price": str(trade.entry_price),
                            "stop_price": str(trade.stop_price),
                            "take_price": str(trade.take_price),
                        },
                    )
                )
            if (
                trade.status.value == "closed"
                and trade_statuses.get(trade.id) != trade.status
            ):
                emitted.append(
                    self._event(
                        sequence,
                        trade.exit_time or trade.entry_time,
                        "trade.closed",
                        trade.setup_id,
                        {
                            "trade_id": trade.id,
                            "exit_reason": trade.exit_reason,
                            "exit_price": str(trade.exit_price),
                            "pnl": str(trade.pnl),
                        },
                    )
                )
        return emitted

    def play(self) -> dict[str, Any]:
        if self._status in {ReplayStatus.COMPLETED, ReplayStatus.CANCELLED}:
            return self.snapshot()
        self._status = ReplayStatus.PLAYING
        while self._next_index < len(self._source_candles) and self._status == ReplayStatus.PLAYING:
            self.step()
            if self._next_index < len(self._source_candles):
                self._status = ReplayStatus.PLAYING
        if self._next_index >= len(self._source_candles):
            self._status = ReplayStatus.COMPLETED
        return self.snapshot()

    def checkpoint(self) -> dict[str, Any]:
        """Return a durable cursor checkpoint; state can be rebuilt causally."""
        return {"run_id": self.run_id, "sequence": self._cursor.sequence if self._cursor else 0}

    def restore(self, checkpoint: dict[str, Any]) -> dict[str, Any]:
        sequence = checkpoint.get("sequence")
        if checkpoint.get("run_id") != self.run_id or not isinstance(sequence, int):
            raise ValueError("checkpoint does not belong to this replay")
        if sequence < 0 or sequence > len(self._source_candles):
            raise ValueError("checkpoint sequence is outside replay range")
        self.reset()
        for _ in range(sequence):
            self.step()
        return self.snapshot()

    def snapshot(self, recent_events: list[LevelEvent] | None = None) -> dict[str, Any]:
        cursor_time = self._cursor.bar_time if self._cursor else None
        visible_master = [
            candle
            for candle in self._master_candles
            if candle.open_time >= self.window.display_from
            and (cursor_time is None or candle.close_time <= cursor_time)
        ]
        visible_detail = [
            candle
            for candle in self._detail_candles
            if cursor_time is not None
            and candle.close_time <= cursor_time
            and candle.open_time >= self.window.display_from
        ]
        return as_json(
            {
                "run_id": self.run_id,
                "status": self._status,
                "cursor": self._cursor,
                "total_candles": len(self._source_candles),
                "pivots": self._pivots,
                "levels": self._levels.levels,
                "outcomes": self._outcomes.outcomes,
                "setups": self._confirmation.setups,
                "trades": self._execution.trades,
                "master_candles": visible_master,
                "detail_candles": visible_detail,
                "events": recent_events if recent_events is not None else self._events,
            }
        )

    def _event(
        self,
        sequence: int,
        event_time: datetime,
        event_type: str,
        level_id: str,
        payload: dict[str, Any],
    ) -> LevelEvent:
        return LevelEvent(
            run_id=self.run_id,
            sequence=sequence,
            event_time=event_time,
            event_type=event_type,
            level_id=level_id,
            reason=event_type,
            payload=payload,
        )
