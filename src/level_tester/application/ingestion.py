from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from time import sleep

from level_tester.domain.models import Candle
from level_tester.infrastructure.binance import BinanceFuturesClient
from level_tester.infrastructure.quality import DataQualityReport, inspect_candles
from level_tester.infrastructure.repositories import CandleRepository


class DataQualityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IngestionResult:
    timeframe: str
    candles: list[Candle]
    quality: DataQualityReport


@dataclass(frozen=True, slots=True)
class CandleCoverage:
    timeframe: str
    required_from: datetime
    required_to: datetime
    stored_count: int
    required_count: int
    missing_ranges: list[tuple[datetime, datetime]]

    @property
    def missing_count(self) -> int:
        return self.required_count - self.stored_count


class DataIngestionService:
    def __init__(
        self, client: BinanceFuturesClient, retries: int = 3, backoff_seconds: float = 0.25
    ) -> None:
        self.client = client
        self.retries = max(0, retries)
        self.backoff_seconds = max(0, backoff_seconds)

    def load(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> IngestionResult:
        candles: list[Candle] | None = None
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                candles = self.client.klines(symbol, timeframe, start, end)
                break
            except Exception as exc:  # noqa: BLE001 - adapter errors share one retry boundary
                last_error = exc
                if attempt < self.retries:
                    sleep(self.backoff_seconds * (2**attempt))
        if candles is None:
            raise RuntimeError("failed to ingest Binance data after retries") from last_error
        step = _timeframe_step(timeframe)
        quality = inspect_candles(candles, step)
        if not quality.is_valid:
            raise DataQualityError(f"invalid {timeframe} data: {quality}")
        return IngestionResult(timeframe, candles, quality)

    def coverage(
        self, session, instrument_id: int, timeframe: str, start: datetime, end: datetime
    ) -> CandleCoverage:
        step = _timeframe_step(timeframe)
        repository = CandleRepository()
        stored = repository.open_times(session, instrument_id, timeframe, start, end)
        expected: list[datetime] = []
        cursor = start
        while cursor < end:
            expected.append(cursor)
            cursor += step
        missing_ranges: list[tuple[datetime, datetime]] = []
        missing_start: datetime | None = None
        for timestamp in expected:
            if timestamp not in stored and missing_start is None:
                missing_start = timestamp
            if timestamp in stored and missing_start is not None:
                missing_ranges.append((missing_start, timestamp))
                missing_start = None
        if missing_start is not None:
            missing_ranges.append((missing_start, expected[-1] + step if expected else end))
        return CandleCoverage(timeframe, start, end, len(stored), len(expected), missing_ranges)

    def ensure_range(
        self,
        session,
        instrument_id: int,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        progress=None,
    ) -> CandleCoverage:
        repository = CandleRepository()
        coverage = self.coverage(session, instrument_id, timeframe, start, end)
        total = max(coverage.missing_count, 1)
        loaded = 0
        for missing_start, missing_end in coverage.missing_ranges:
            result = self.load(symbol, timeframe, missing_start, missing_end)
            repository.upsert_many(session, instrument_id, result.candles)
            loaded += len(result.candles)
            if progress:
                progress(min(99, int(loaded * 100 / total)))
        return self.coverage(session, instrument_id, timeframe, start, end)


def _timeframe_step(timeframe: str) -> timedelta:
    if timeframe == "1h":
        return timedelta(hours=1)
    if timeframe == "1m":
        return timedelta(minutes=1)
    if timeframe == "5m":
        return timedelta(minutes=5)
    raise ValueError("supported timeframes are 1m, 5m and 1h")
