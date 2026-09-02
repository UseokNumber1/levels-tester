"""Core backtest engine: runs signals through execution variants."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Optional

from level_tester.backtester.candle_loader import CandleLoader
from level_tester.backtester.entry_types import EntryResult, EntryType, resolve_entry
from level_tester.backtester.signal_reader import SignalEntry
from level_tester.backtester.trade_model import BacktestTrade
from level_tester.backtester.variants import Variant
from level_tester.domain.models import Candle
from level_tester.infrastructure.binance import BinanceFuturesClient


@dataclass(slots=True)
class TradeResult:
    """Result of one signal tested with one variant."""
    signal: SignalEntry
    variant: Variant
    entry_type: EntryType
    entry: Optional[EntryResult]
    trade: Optional[BacktestTrade]
    candles_used: int
    error: Optional[str] = None
    method: int = 2
    method_label: str = "2 bars"


class BacktestEngine:
    """Run signals through execution variants on historical candles."""

    def __init__(self, client: BinanceFuturesClient) -> None:
        self.loader = CandleLoader(client)

    def run_signal(
        self,
        signal: SignalEntry,
        variants: list[Variant],
        entry_type: EntryType = EntryType.MARKET,
        lookback_bars: int = 0,
        lookforward_bars: int = 1000,
        limit_offset_pct: float = 0.2,
        confirmation_bars: int = 2,
        confirmation_max_wait: int = 15,
    ) -> list[TradeResult]:
        """Test one signal against multiple variants.

        Args:
            signal: the signal to test
            variants: list of execution variants to try
            entry_type: how to enter the trade
            lookback_bars: candles to load before signal (default 0)
            lookforward_bars: candles to load after signal (default 1000)
            limit_offset_pct: offset for LIMIT entry
            confirmation_bars: bars required for CONFIRMATION entry (0=touch, 1=1 bar, 2=2 bars)
            confirmation_max_wait: max bars to wait for CONFIRMATION after touch

        Returns:
            List of TradeResult, one per variant
        """
        results: list[TradeResult] = []

        # Load candles once for all variants (always on 5m for precise entry)
        try:
            signal_time = self._parse_signal_time(signal)
            candles = self.loader.load(
                signal.symbol, "5m", signal_time,
                lookback_bars=lookback_bars,
                lookforward_bars=lookforward_bars,
            )
        except Exception as exc:
            for variant in variants:
                results.append(TradeResult(
                    signal=signal, variant=variant, entry_type=entry_type,
                    entry=None, trade=None, candles_used=0,
                    error=f"candle load failed: {exc}",
                ))
            return results

        # Resolve entry (same for all variants)
        entry = resolve_entry(
            entry_type=entry_type,
            side=signal.side,
            signal_entry_price=signal.entry_price,
            signal_time=signal_time,
            candles=candles,
            limit_offset_pct=limit_offset_pct,
            confirmation_bars=confirmation_bars,
            confirmation_max_wait=confirmation_max_wait,
        )

        if entry is None:
            for variant in variants:
                results.append(TradeResult(
                    signal=signal, variant=variant, entry_type=entry_type,
                    entry=None, trade=None, candles_used=len(candles),
                    error="entry not triggered",
                ))
            return results

        # Find the index of entry candle
        entry_candle_index = 0
        for i, candle in enumerate(candles):
            if candle.open_time >= entry.entry_time:
                entry_candle_index = i
                break

        # Run each variant from entry point
        for variant in variants:
            trade = self._run_variant(
                signal, variant, entry, candles, entry_candle_index, signal_time
            )
            results.append(TradeResult(
                signal=signal, variant=variant, entry_type=entry_type,
                entry=entry, trade=trade, candles_used=len(candles),
            ))

        return results

    def run_all(
        self,
        signals: list[SignalEntry],
        variants: list[Variant],
        entry_type: EntryType = EntryType.MARKET,
        lookback_bars: int = 0,
        lookforward_bars: int = 1000,
        limit_offset_pct: float = 0.2,
        confirmation_bars: int = 2,
        confirmation_max_wait: int = 15,
        progress_callback=None,
    ) -> list[TradeResult]:
        """Run all signals through all variants."""
        all_results: list[TradeResult] = []
        total = len(signals)

        for i, signal in enumerate(signals):
            if progress_callback:
                progress_callback(i + 1, total, signal.symbol)

            results = self.run_signal(
                signal, variants, entry_type,
                lookback_bars=lookback_bars,
                lookforward_bars=lookforward_bars,
                limit_offset_pct=limit_offset_pct,
                confirmation_bars=confirmation_bars,
                confirmation_max_wait=confirmation_max_wait,
            )
            all_results.extend(results)

        return all_results

    def _run_variant(
        self,
        signal: SignalEntry,
        variant: Variant,
        entry: EntryResult,
        candles: list[Candle],
        entry_candle_index: int,
        signal_time: datetime,
    ) -> BacktestTrade:
        """Create and run a BacktestTrade for one variant."""
        # Compute SL/TP
        entry_price = entry.entry_price
        is_long = signal.side == "LONG"
        risk_distance = entry_price * variant.sl_pct / Decimal(100)

        if is_long:
            stop_price = entry_price - risk_distance
        else:
            stop_price = entry_price + risk_distance

        # Take profit
        if variant.tp_pct is not None:
            tp_distance = entry_price * variant.tp_pct / Decimal(100)
            if is_long:
                take_price = entry_price + tp_distance
            else:
                take_price = entry_price - tp_distance
        elif variant.tp_rr is not None:
            tp_distance = risk_distance * variant.tp_rr
            if is_long:
                take_price = entry_price + tp_distance
            else:
                take_price = entry_price - tp_distance
        else:
            # Fallback: RR 2
            tp_distance = risk_distance * Decimal("2")
            if is_long:
                take_price = entry_price + tp_distance
            else:
                take_price = entry_price - tp_distance

        trade = BacktestTrade(
            id=f"{signal.signal_id}_{variant.id}",
            side=signal.side,
            entry_time=entry.entry_time,
            entry_price=entry_price,
            stop_price=stop_price,
            take_price=take_price,
            trailing_stop_pct=variant.trailing_stop_pct,
            trailing_activation_pct=variant.trailing_activation_pct,
            trailing_update_threshold_pct=variant.trailing_update_threshold_pct,
            trailing_tp_only=variant.trailing_tp_only,
            breakeven_trigger_pct=variant.breakeven_trigger_pct,
            breakeven_lock_pct=variant.breakeven_lock_pct,
            partial_close_pct=variant.partial_close_pct,
            partial_close_rr=variant.partial_close_rr,
        )

        # Run through candles from entry point with reloading
        candles = self._run_with_reload(trade, signal, candles, entry_candle_index, signal_time)

        return trade

    def _run_with_reload(
        self,
        trade: BacktestTrade,
        signal: SignalEntry,
        initial_candles: list[Candle],
        entry_candle_index: int,
        signal_time: datetime,
    ) -> list[Candle]:
        """Run trade through candles, reloading when exhausted."""
        candles = initial_candles
        candle_offset = 0  # tracks total candles processed from entry

        while True:
            # Process current batch
            for i in range(entry_candle_index, len(candles)):
                candle = candles[i]
                closed = trade.tick(
                    candle_high=candle.high,
                    candle_low=candle.low,
                    candle_close=candle.close,
                    candle_time=candle.close_time,
                    candle_index=candle_offset + i - entry_candle_index,
                )
                if closed:
                    return candles

            # If we exited the loop without closing, we need more candles
            # Reload next batch starting from the last candle's close_time
            last_candle = candles[-1]
            try:
                new_candles = self.loader.load(
                    signal.symbol, "5m", last_candle.close_time,
                    lookback_bars=0,
                    lookforward_bars=1000,
                )
            except Exception:
                break  # No more data available

            if not new_candles:
                break

            # Skip the first candle as it overlaps with the last one we processed
            # (last_candle.close_time == new_candles[0].open_time)
            candles = new_candles[1:]
            entry_candle_index = 0
            candle_offset += len(new_candles) - 1

        return candles

    @staticmethod
    def _parse_signal_time(signal: SignalEntry) -> datetime:
        """Parse signal timestamp to UTC datetime."""
        if signal.timestamp:
            try:
                dt = datetime.fromisoformat(signal.timestamp)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt.astimezone(UTC)
            except (ValueError, TypeError):
                pass
        return datetime.now(UTC)
