"""Extended trade model with trailing stop, breakeven, and partial close."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional


@dataclass(slots=True)
class BacktestTrade:
    """Trade with extended exit logic for backtesting."""

    id: str
    side: str  # LONG | SHORT
    entry_time: datetime
    entry_price: Decimal
    stop_price: Decimal
    take_price: Decimal

    # Trailing Stop
    trailing_stop_pct: Optional[Decimal] = None
    trailing_activation_pct: Optional[Decimal] = None
    trailing_update_threshold_pct: Optional[Decimal] = None
    trailing_activated: bool = False
    trailing_extreme: Optional[Decimal] = None  # max(HIGH) for LONG, min(LOW) for SHORT
    trailing_current_stop: Optional[Decimal] = None
    trailing_tp_only: bool = False

    # Breakeven
    breakeven_trigger_pct: Optional[Decimal] = None
    breakeven_lock_pct: Optional[Decimal] = None
    breakeven_active: bool = False

    # Partial Close
    partial_close_pct: Optional[Decimal] = None  # e.g. 50 means close 50%
    partial_close_rr: Optional[Decimal] = None    # close at this RR
    partial_closed: bool = False
    original_stop_price: Optional[Decimal] = None

    # Result
    status: str = "open"  # open | closed
    exit_time: Optional[datetime] = None
    exit_price: Optional[Decimal] = None
    exit_reason: Optional[str] = None  # stop_loss | take_profit | trailing_stop | breakeven
    pnl: Optional[Decimal] = None
    pnl_pct: Optional[Decimal] = None
    bars_held: int = 0

    def tick(self, candle_high: Decimal, candle_low: Decimal, candle_close: Decimal,
             candle_time: datetime, candle_index: int) -> bool:
        """Process one candle. Returns True if trade is closed."""
        if self.status != "open":
            return True

        self.bars_held = candle_index

        is_long = self.side == "LONG"
        risk_distance = abs(self.entry_price - self.stop_price)

        # --- 1. Check Breakeven ---
        if self.breakeven_trigger_pct is not None and not self.breakeven_active:
            trigger_distance = self.entry_price * self.breakeven_trigger_pct / Decimal(100)
            if is_long:
                if candle_high >= self.entry_price + trigger_distance:
                    self._activate_breakeven(is_long)
            else:
                if candle_low <= self.entry_price - trigger_distance:
                    self._activate_breakeven(is_long)

        # --- 2. Check Trailing Stop activation ---
        if (self.trailing_activation_pct is not None
                and not self.trailing_activated
                and self.trailing_stop_pct is not None):
            activation_distance = self.entry_price * self.trailing_activation_pct / Decimal(100)
            if is_long:
                if candle_high >= self.entry_price + activation_distance:
                    self.trailing_activated = True
                    self.trailing_extreme = candle_high
                    new_stop = candle_high * (Decimal(1) - self.trailing_stop_pct / Decimal(100))
                    if self.trailing_current_stop is None or new_stop > self.trailing_current_stop:
                        self.trailing_current_stop = new_stop
            else:
                if candle_low <= self.entry_price - activation_distance:
                    self.trailing_activated = True
                    self.trailing_extreme = candle_low
                    new_stop = candle_low * (Decimal(1) + self.trailing_stop_pct / Decimal(100))
                    if self.trailing_current_stop is None or new_stop < self.trailing_current_stop:
                        self.trailing_current_stop = new_stop

        # --- 3. Update Trailing Stop ---
        if self.trailing_activated and self.trailing_stop_pct is not None:
            threshold = self.trailing_update_threshold_pct or Decimal("0.1")
            if is_long:
                if candle_high > (self.trailing_extreme or candle_high):
                    self.trailing_extreme = candle_high
                    new_stop = candle_high * (Decimal(1) - self.trailing_stop_pct / Decimal(100))
                    if (self.trailing_current_stop is None
                            or new_stop - self.trailing_current_stop >= self.entry_price * threshold / Decimal(100)):
                        self.trailing_current_stop = new_stop
            else:
                if candle_low < (self.trailing_extreme or candle_low):
                    self.trailing_extreme = candle_low
                    new_stop = candle_low * (Decimal(1) + self.trailing_stop_pct / Decimal(100))
                    if (self.trailing_current_stop is None
                            or self.trailing_current_stop - new_stop >= self.entry_price * threshold / Decimal(100)):
                        self.trailing_current_stop = new_stop

        # --- 4. Partial Close ---
        if (self.partial_close_pct is not None
                and self.partial_close_rr is not None
                and not self.partial_closed):
            target_price = self._partial_close_target(is_long, risk_distance)
            if target_price is not None:
                if is_long and candle_high >= target_price:
                    self._do_partial_close(target_price, candle_time)
                elif not is_long and candle_low <= target_price:
                    self._do_partial_close(target_price, candle_time)

        # --- 5. Determine effective stop ---
        effective_stop = self.stop_price
        if self.trailing_activated and self.trailing_current_stop is not None:
            if is_long and self.trailing_current_stop > effective_stop:
                effective_stop = self.trailing_current_stop
            elif not is_long and self.trailing_current_stop < effective_stop:
                effective_stop = self.trailing_current_stop
        if self.breakeven_active and self.original_stop_price is not None:
            effective_stop = self.original_stop_price

        # --- 6. Check Stop Loss ---
        if is_long:
            if candle_low <= effective_stop:
                self._close(effective_stop, candle_time, "stop_loss" if not self.trailing_activated else "trailing_stop")
                return True
        else:
            if candle_high >= effective_stop:
                self._close(effective_stop, candle_time, "stop_loss" if not self.trailing_activated else "trailing_stop")
                return True

        # --- 7. Check Take Profit ---
        if not self.trailing_tp_only:
            if is_long:
                if candle_high >= self.take_price:
                    self._close(self.take_price, candle_time, "take_profit")
                    return True
            else:
                if candle_low <= self.take_price:
                    self._close(self.take_price, candle_time, "take_profit")
                    return True

        # --- 8. Check 20% distance from entry (level invalidated) ---
        price_move_pct = abs(candle_close - self.entry_price) / self.entry_price * Decimal(100)
        if price_move_pct >= Decimal("20"):
            self._close(candle_close, candle_time, "level_invalidated")
            return True

        return False

    def _activate_breakeven(self, is_long: bool) -> None:
        self.breakeven_active = True
        lock_distance = self.entry_price * self.breakeven_lock_pct / Decimal(100)
        if is_long:
            self.original_stop_price = self.stop_price
            new_stop = self.entry_price + lock_distance
            if new_stop > self.stop_price:
                self.stop_price = new_stop
        else:
            self.original_stop_price = self.stop_price
            new_stop = self.entry_price - lock_distance
            if new_stop < self.stop_price:
                self.stop_price = new_stop

    def _partial_close_target(self, is_long: bool, risk_distance: Decimal) -> Optional[Decimal]:
        rr_distance = risk_distance * self.partial_close_rr
        if is_long:
            return self.entry_price + rr_distance
        return self.entry_price - rr_distance

    def _do_partial_close(self, price: Decimal, time: datetime) -> None:
        self.partial_closed = True
        # After partial close, move stop to breakeven for remaining position
        if self.original_stop_price is None:
            self.original_stop_price = self.stop_price
        lock_distance = self.entry_price * (self.breakeven_lock_pct or Decimal("0.35")) / Decimal(100)
        if self.side == "LONG":
            new_stop = self.entry_price + lock_distance
            if new_stop > self.stop_price:
                self.stop_price = new_stop
        else:
            new_stop = self.entry_price - lock_distance
            if new_stop < self.stop_price:
                self.stop_price = new_stop

    def _close(self, price: Decimal, time: datetime, reason: str) -> None:
        self.status = "closed"
        self.exit_price = price
        self.exit_time = time
        self.exit_reason = reason
        multiplier = Decimal(1) if self.side == "LONG" else Decimal(-1)
        self.pnl = (price - self.entry_price) * multiplier
        if self.entry_price > 0:
            self.pnl_pct = self.pnl / self.entry_price * Decimal(100)
