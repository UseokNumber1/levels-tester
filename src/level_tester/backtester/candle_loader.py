"""Load historical candles, shared-DB-first via BinanceFuturesClient fallback."""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from level_tester.domain.models import Candle
from level_tester.infrastructure.binance import BinanceFuturesClient


class CandleLoader:
    """Load candles for a symbol/timeframe around a signal timestamp.

    When a session factory is available (explicit or via ``DATABASE_URL``),
    reads go through the shared ``candles`` table first
    (``application.candle_service.get_candles``: gaps are backfilled once,
    then served from SQL). Any DB failure falls back to the legacy direct
    exchange load — backtests never break because of storage.
    """

    def __init__(self, client: BinanceFuturesClient, session_factory=None) -> None:
        self.client = client
        self._session_factory = session_factory

    def _default_factory(self):
        if self._session_factory is not None:
            return self._session_factory
        dsn = os.environ.get("DATABASE_URL", "").strip()
        if not dsn or "sqlite" in dsn and ":memory:" in dsn:
            return None
        if not dsn:
            return None
        try:
            from level_tester.infrastructure.database import create_session_factory
            self._session_factory = create_session_factory(dsn)
            return self._session_factory
        except Exception:
            return None

    def load(
        self,
        symbol: str,
        timeframe: str,
        signal_time: datetime,
        lookback_bars: int = 50,
        lookforward_bars: int = 200,
    ) -> list[Candle]:
        """Load candles centered on signal_time.

        Args:
            symbol: e.g. 'BTCUSDT'
            timeframe: '1m', '5m', '15m', '1h', '4h', '1d'
            signal_time: the signal timestamp (UTC, timezone-aware)
            lookback_bars: how many bars before signal_time to load
            lookforward_bars: how many bars after signal_time to load

        Returns:
            Sorted list of Candle objects
        """
        signal_time = signal_time.astimezone(UTC)
        bar_duration = _timeframe_to_duration(timeframe)
        start = signal_time - lookback_bars * bar_duration
        end = signal_time + lookforward_bars * bar_duration
        shared = self._load_shared(symbol, timeframe, start, end)
        if shared is not None:
            return shared
        return self.client.klines(symbol, timeframe, start, end)

    def load_range(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        """Load all candles in a date range."""
        start = start.astimezone(UTC)
        end = end.astimezone(UTC)
        shared = self._load_shared(symbol, timeframe, start, end)
        if shared is not None:
            return shared
        return self.client.klines(symbol, timeframe, start, end)

    def _load_shared(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle] | None:
        """Shared-table read, None = fall back to direct exchange load."""
        try:
            factory = self._default_factory()
            if factory is None:
                return None
            from level_tester.application.candle_service import (
                get_candles, last_closed_open, timeframe_step,
            )
            step = timeframe_step(timeframe)
            live_edge = last_closed_open(timeframe) - step
            tail_since = live_edge if end > live_edge else None
            return get_candles(
                factory,
                lambda s, tf, a, b: self.client.klines(s, tf, a, b),
                symbol, timeframe, start, end,
                refresh_tail_since=tail_since,
            )
        except Exception:
            return None


def _timeframe_to_duration(tf: str) -> timedelta:
    mapping = {
        "1m": timedelta(minutes=1),
        "3m": timedelta(minutes=3),
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "30m": timedelta(minutes=30),
        "1h": timedelta(hours=1),
        "2h": timedelta(hours=2),
        "4h": timedelta(hours=4),
        "6h": timedelta(hours=6),
        "8h": timedelta(hours=8),
        "12h": timedelta(hours=12),
        "1d": timedelta(days=1),
    }
    if tf not in mapping:
        raise ValueError(f"unsupported timeframe: {tf}")
    return mapping[tf]
