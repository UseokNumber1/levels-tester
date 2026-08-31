"""Load historical candles from Binance via BinanceFuturesClient."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from level_tester.domain.models import Candle
from level_tester.infrastructure.binance import BinanceFuturesClient


class CandleLoader:
    """Load candles for a symbol/timeframe around a signal timestamp."""

    def __init__(self, client: BinanceFuturesClient) -> None:
        self.client = client

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
        return self.client.klines(symbol, timeframe, start, end)


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
