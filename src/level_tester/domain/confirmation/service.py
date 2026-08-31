from __future__ import annotations

from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from level_tester.domain.confirmation.models import ConfirmationConfig, TradeSetup, TradeSetupStatus
from level_tester.domain.models import Candle, Level, LevelEvent, LevelSide


class EntryConfirmation:
    """Turn a confirmed H1 touch into a deterministic trade setup."""

    def __init__(self, run_id: str, config: ConfirmationConfig | None = None) -> None:
        self.run_id = run_id
        self.config = config or ConfirmationConfig()
        self.setups: list[TradeSetup] = []
        self._last_detail_time: dict[str, datetime] = {}

    def evaluate(
        self,
        detail_candles: list[Candle],
        levels: list[Level],
        events: list[LevelEvent],
        sequence: int,
    ) -> list[TradeSetup]:
        touched_events = {
            event.level_id: event
            for event in events
            if event.event_type == "level.touched"
        }
        for level in levels:
            touch_event = touched_events.get(level.id)
            if touch_event is not None and not self._has_setup_for_touch(
                level.id, touch_event.event_time
            ):
                touch_bar_open = _event_time(touch_event, "candle_open_time")
                touch_bar_close = _event_time(touch_event, "candle_close_time")
                self.setups.append(
                    TradeSetup(
                        id=str(
                            uuid5(
                                NAMESPACE_URL,
                                f"{self.run_id}:{self.config.method}:{level.id}:{level.touch_count}",
                            )
                        ),
                        level_id=level.id,
                        side=level.side,
                        touch_time=touch_event.event_time,
                        source_touch_time=touch_event.event_time,
                        touch_bar_open_time=touch_bar_open,
                        touch_bar_close_time=touch_bar_close,
                        confirmation_method=self.config.method,
                    )
                )

        for setup in self.setups:
            if setup.status == TradeSetupStatus.ENTRY_CONFIRMED and setup.entry_time is None:
                self._set_entry(setup, detail_candles)
            if setup.status != TradeSetupStatus.WAITING_CONFIRMATION:
                continue
            level = next((item for item in levels if item.id == setup.level_id), None)
            if level is None:
                continue
            if not setup.touch_refined:
                self._refine_touch(setup, level, detail_candles)
            if self.config.method == "touch":
                setup.status = TradeSetupStatus.ENTRY_CONFIRMED
                setup.confirmed_time = setup.touch_time
                setup.reason = "touch entry"
                self._set_entry(setup, detail_candles)
                continue
            candles = [
                candle
                for candle in detail_candles
                if candle.open_time > setup.touch_time
                and (
                    setup.id not in self._last_detail_time
                    or candle.open_time > self._last_detail_time[setup.id]
                )
            ]
            for candle in candles:
                setup.bars_waited += 1
                setup.confirmation_bars = (
                    setup.confirmation_bars + 1
                    if self._is_confirming(candle, level)
                    else 0
                )
                if setup.confirmation_bars >= self.config.required_bars:
                    setup.status = TradeSetupStatus.ENTRY_CONFIRMED
                    setup.confirmed_time = candle.close_time
                    setup.reason = "confirmation pattern reached"
                    break
            if candles:
                self._last_detail_time[setup.id] = max(candle.open_time for candle in candles)
            if (
                setup.status == TradeSetupStatus.WAITING_CONFIRMATION
                and setup.bars_waited >= self.config.max_wait_bars
                and candles
            ):
                self._cancel(setup, candles[-1].close_time, "confirmation timeout")
        return list(self.setups)

    def _has_setup_for_touch(self, level_id: str, touch_time: datetime) -> bool:
        return any(
            setup.level_id == level_id
            and (setup.source_touch_time or setup.touch_time) == touch_time
            for setup in self.setups
        )

    def _is_confirming(self, candle: Candle, level: Level) -> bool:
        if level.side == LevelSide.SUPPORT:
            in_direction = candle.close > level.price
            if self.config.method == "bounce":
                return candle.close > candle.open and in_direction
            return in_direction
        in_direction = candle.close < level.price
        if self.config.method == "bounce":
            return candle.close < candle.open and in_direction
        return in_direction

    def _set_entry(self, setup: TradeSetup, detail_candles: list[Candle]) -> None:
        if setup.confirmed_time is None:
            return
        next_candle = next(
            (candle for candle in detail_candles if candle.open_time >= setup.confirmed_time),
            None,
        )
        if next_candle is None:
            return
        setup.entry_time = next_candle.open_time
        setup.entry_price = next_candle.open if self.config.entry_on_next_bar else next_candle.close

    @staticmethod
    def _refine_touch(
        setup: TradeSetup, level: Level, detail_candles: list[Candle]
    ) -> None:
        if setup.touch_bar_open_time is None or setup.touch_bar_close_time is None:
            setup.touch_refined = True
            return
        for candle in detail_candles:
            if not (
                setup.touch_bar_open_time <= candle.open_time < setup.touch_bar_close_time
            ):
                continue
            relevant_price = candle.high if level.side == LevelSide.RESISTANCE else candle.low
            reached_level = (
                relevant_price >= level.price
                if level.side == LevelSide.RESISTANCE
                else relevant_price <= level.price
            )
            if reached_level:
                setup.touch_time = candle.close_time
                setup.touch_refined = True
                return

    @staticmethod
    def _cancel(setup: TradeSetup, cancelled_time: datetime, reason: str) -> None:
        setup.status = TradeSetupStatus.CANCELLED
        setup.cancelled_time = cancelled_time
        setup.reason = reason


def _event_time(event: LevelEvent, key: str) -> datetime | None:
    value = event.payload.get(key)
    if not isinstance(value, str):
        return None
    return datetime.fromisoformat(value)
