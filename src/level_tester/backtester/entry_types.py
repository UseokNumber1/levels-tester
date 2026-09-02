"""Three entry types: market, limit, confirmation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Optional

from level_tester.domain.models import Candle


class EntryType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    CONFIRMATION = "confirmation"


@dataclass(frozen=True, slots=True)
class EntryResult:
    entry_time: datetime
    entry_price: Decimal
    bars_waited: int  # how many bars from signal_time to entry


def resolve_entry(
    entry_type: EntryType,
    side: str,
    signal_entry_price: float,
    signal_time: datetime,
    candles: list[Candle],
    limit_offset_pct: float = 0.2,
    confirmation_bars: int = 2,
    confirmation_max_wait: int = 15,
    confirmation_tf: str = "5m",
) -> Optional[EntryResult]:
    """Determine entry point based on entry type.

    Args:
        entry_type: MARKET, LIMIT, or CONFIRMATION
        side: LONG or SHORT
        signal_entry_price: the level from the signal
        signal_time: when the signal was created (UTC)
        candles: all loaded candles (sorted by open_time)
        limit_offset_pct: for LIMIT: offset from entry_price (%)
        confirmation_bars: for CONFIRMATION: 0=touch, 1=1 bar, 2=2 bars
        confirmation_max_wait: for CONFIRMATION: max bars to wait for confirmation after touch
        confirmation_tf: for CONFIRMATION: timeframe to check (must match candle timeframe)

    Returns:
        EntryResult or None if entry never triggered
    """
    entry_price = Decimal(str(signal_entry_price))

    if entry_type == EntryType.MARKET:
        return _entry_market(side, entry_price, signal_time, candles)

    if entry_type == EntryType.LIMIT:
        return _entry_limit(side, entry_price, signal_time, candles, limit_offset_pct)

    if entry_type == EntryType.CONFIRMATION:
        return _entry_touch_market(
            side, entry_price, signal_time, candles,
            confirmation_bars, confirmation_max_wait,
        )

    raise ValueError(f"unknown entry type: {entry_type}")


def _entry_market(
    side: str, entry_price: Decimal, signal_time: datetime, candles: list[Candle]
) -> Optional[EntryResult]:
    """Enter at the first candle after signal_time."""
    for i, candle in enumerate(candles):
        if candle.open_time >= signal_time:
            price = candle.open  # market entry at open
            return EntryResult(entry_time=candle.open_time, entry_price=price, bars_waited=i)
    return None


def _entry_limit(
    side: str, entry_price: Decimal, signal_time: datetime,
    candles: list[Candle], offset_pct: float,
) -> Optional[EntryResult]:
    """Enter when price reaches entry_price ± offset."""
    offset = entry_price * Decimal(str(offset_pct)) / Decimal(100)

    if side == "LONG":
        limit_price = entry_price - offset  # buy lower
    else:
        limit_price = entry_price + offset  # sell higher

    for i, candle in enumerate(candles):
        if candle.open_time < signal_time:
            continue
        if side == "LONG":
            if candle.low <= limit_price:
                return EntryResult(
                    entry_time=candle.open_time,
                    entry_price=limit_price,
                    bars_waited=i,
                )
        else:
            if candle.high >= limit_price:
                return EntryResult(
                    entry_time=candle.open_time,
                    entry_price=limit_price,
                    bars_waited=i,
                )
    return None


def _entry_touch_market(
    side: str, entry_price: Decimal, signal_time: datetime,
    candles: list[Candle], required_bars: int, max_wait: int,
) -> Optional[EntryResult]:
    """
    Touch-based entry with confirmation bars.

    Logic:
    1. Find first valid touch after signal_time:
       - LONG: candle.low <= entry_price AND candle.close > entry_price AND candle.close > candle.open (green)
       - SHORT: candle.high >= entry_price AND candle.close < entry_price AND candle.close < candle.open (red)
    2. After valid touch, scan up to max_wait bars for required_bars consecutive confirming bars:
       - confirming bar LONG: close > entry_price AND close > open (green)
       - confirming bar SHORT: close < entry_price AND close < open (red)
    3. Entry:
       - required_bars=0 (Touch): LIMIT at entry_price on touch candle (already placed, fills at touch)
       - required_bars=1 or 2: MARKET at next bar's open after confirmation reached
    4. If max_wait exceeded without confirmation -> reset, search for new touch
    5. If touch invalid (close not in direction) -> continue searching for new touch
    """
    touch_found = False
    touch_index = 0
    consecutive = 0

    for i, candle in enumerate(candles):
        if candle.open_time < signal_time:
            continue

        # Step 1: Find valid touch
        if not touch_found:
            if side == "LONG":
                if candle.low <= entry_price:
                    # Check if touch is valid: closed above entry AND green
                    if candle.close > entry_price and candle.close > candle.open:
                        touch_found = True
                        touch_index = i
                        consecutive = 0
                    # else: touch invalid (closed below entry or red), continue searching
            else:  # SHORT
                if candle.high >= entry_price:
                    # Check if touch is valid: closed below entry AND red
                    if candle.close < entry_price and candle.close < candle.open:
                        touch_found = True
                        touch_index = i
                        consecutive = 0
                    # else: touch invalid, continue searching
            continue

        # Step 2: Scan for confirmation within max_wait bars after touch
        bars_since_touch = i - touch_index
        if bars_since_touch > max_wait:
            # Timeout on confirmation - reset and search for new touch
            touch_found = False
            consecutive = 0
            continue

        # Check if current candle is confirming
        if side == "LONG":
            is_confirming = candle.close > entry_price and candle.close > candle.open
        else:  # SHORT
            is_confirming = candle.close < entry_price and candle.close < candle.open

        if is_confirming:
            consecutive += 1
        else:
            consecutive = 0  # reset counter but keep touch_found

        # Step 3: Check if confirmation reached
        if consecutive >= required_bars:
            # Type 0: LIMIT entry at entry_price on touch candle
            if required_bars == 0:
                return EntryResult(
                    entry_time=candles[touch_index].open_time,
                    entry_price=entry_price,
                    bars_waited=touch_index,
                )
            # Type 1 or 2: MARKET entry on next bar's open
            next_idx = i + 1
            if next_idx < len(candles):
                next_candle = candles[next_idx]
                return EntryResult(
                    entry_time=next_candle.open_time,
                    entry_price=next_candle.open,
                    bars_waited=next_idx,
                )
            return None

    return None
