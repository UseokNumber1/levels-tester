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
        confirmation_bars: for CONFIRMATION: N consecutive bars required
        confirmation_max_wait: for CONFIRMATION: max bars to wait
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
        return _entry_confirmation(
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


def _entry_confirmation(
    side: str, entry_price: Decimal, signal_time: datetime,
    candles: list[Candle], required_bars: int, max_wait: int,
) -> Optional[EntryResult]:
    """Wait for touch of entry_price, then N consecutive bars in trade direction.

    Logic (from levels-tester EntryConfirmation):
    1. Find first candle after signal_time where price touches entry_price
    2. After touch, count consecutive bars where close is in trade direction
    3. If required_bars consecutive bars reached -> entry on next bar open
    4. If max_wait exceeded -> no entry
    """
    touch_found = False
    touch_index = 0
    consecutive = 0

    for i, candle in enumerate(candles):
        if candle.open_time < signal_time:
            continue

        # Step 1: Find touch
        if not touch_found:
            if side == "LONG" and candle.low <= entry_price:
                touch_found = True
                touch_index = i
            elif side == "SHORT" and candle.high >= entry_price:
                touch_found = True
                touch_index = i
            continue

        # Step 2: Count consecutive bars after touch
        bars_since_touch = i - touch_index
        if bars_since_touch > max_wait:
            return None  # timeout

        if side == "LONG":
            if candle.close > candle.open and candle.close > entry_price:
                consecutive += 1
            else:
                consecutive = 0
        else:
            if candle.close < candle.open and candle.close < entry_price:
                consecutive += 1
            else:
                consecutive = 0

        # Step 3: Confirmation reached
        if consecutive >= required_bars:
            # Entry on next bar's open
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
