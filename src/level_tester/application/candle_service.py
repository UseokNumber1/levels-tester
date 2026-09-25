"""Shared candle read-through service (2026-09-19).

Canonical gap-check + backfill used by all three consumers of the common
``candles`` table (levels-tester itself, MVP_1H scanner, PGv2 engine):

    candles = get_candles(session_factory, fetch_fn, symbol, timeframe, start, end)

- ``fetch_fn(symbol, timeframe, start_dt, end_dt) -> list[Candle]`` is
  caller-provided (each project fetches with its own client/weight
  accounting); storage, gaps and instruments are shared.
- Only CLOSED bars are stored/served (forming bar stays on the exchange).
- Raises on failure — callers decide fallback (PGv2/MVP_1H fall back to
  direct fetch; levels-tester surfaces 503).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Callable

from level_tester.domain.models import Candle
from level_tester.infrastructure.database import InstrumentRow
from level_tester.infrastructure.repositories import CandleRepository

_TIMEFRAME_STEPS: dict[str, timedelta] = {
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


def timeframe_step(timeframe: str) -> timedelta:
    try:
        return _TIMEFRAME_STEPS[timeframe]
    except KeyError:
        raise ValueError(f"unsupported timeframe: {timeframe}") from None


def last_closed_open(timeframe: str, now: datetime | None = None) -> datetime:
    """Open time of the last fully closed bar (UTC)."""
    step = timeframe_step(timeframe)
    now = (now or datetime.now(UTC)).astimezone(UTC)
    step_s = int(step.total_seconds())
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch // step_s * step_s - step_s, tz=UTC)


# Instrument ids are immutable: cache per process to skip a SELECT per call
# (hot loops resolve the same symbols hundreds of times).
_INST_CACHE: dict[str, int] = {}


def ensure_instrument(session, symbol: str) -> int:
    """Get-or-create instruments row for a Binance USDT-M symbol."""
    from sqlalchemy import select

    symbol = symbol.upper()
    cached = _INST_CACHE.get(symbol)
    if cached is not None:
        return cached
    row = session.scalar(select(InstrumentRow).where(InstrumentRow.symbol == symbol))
    if row is not None:
        _INST_CACHE[symbol] = row.id
        return row.id
    row = InstrumentRow(
        symbol=symbol,
        exchange="binance",
        market_type="usdt_m_futures",
        quote_asset="USDT",
        base_asset=symbol[:-4] if symbol.endswith("USDT") else "",
        status="TRADING",
    )
    session.add(row)
    session.commit()
    _INST_CACHE[symbol] = row.id
    return row.id


def missing_ranges(
    stored: set[datetime], start: datetime, end: datetime, step: timedelta
) -> list[tuple[datetime, datetime]]:
    """Contiguous missing [start, end) ranges stepped by bar duration."""
    stored_s = {int(dt.timestamp()) for dt in stored}
    ranges: list[tuple[datetime, datetime]] = []
    cur = start
    rs: datetime | None = None
    while cur < end:
        if int(cur.timestamp()) not in stored_s:
            if rs is None:
                rs = cur
        elif rs is not None:
            ranges.append((rs, cur))
            rs = None
        cur += step
    if rs is not None:
        ranges.append((rs, end))
    void = [r for r in ranges if r[1] > r[0]]
    return void


def get_candles(
    session_factory,
    fetch_fn: Callable[[str, str, datetime, datetime], list[Candle]],
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    refresh_tail_since: datetime | None = None,
    max_bars_per_request: int = 1000,
) -> list[Candle]:
    """Return closed candles in [start, end) via shared table + backfill.

    Missing ranges are fetched through ``fetch_fn`` and upserted. Stored
    bars with ``open_time >= refresh_tail_since`` (when given) are
    re-fetched too — the exchange revises the newest closed bars. The
    forming bar is never stored nor returned. Callers throttle
    ``refresh_tail_since`` themselves (e.g. at most once per bar period);
    historical windows pass None and cost zero requests on hits.
    """
    step = timeframe_step(timeframe)
    symbol = symbol.upper()
    start = start.astimezone(UTC)
    end = min(end.astimezone(UTC), last_closed_open(timeframe) + step)
    # Align the window to EXACT bar boundaries via epoch-second arithmetic.
    # An unaligned start steps through timestamps that never match stored
    # bar opens and fakes a full-window gap; leftover microseconds also
    # EXCLUDE the boundary bar from the DB query (>= comparison) with the
    # same effect (full re-download every call).
    step_s = int(step.total_seconds())
    start = datetime.fromtimestamp(int(start.timestamp()) // step_s * step_s, tz=UTC)
    if end <= start:
        return []
    repository = CandleRepository()
    session = session_factory()
    try:
        instrument_id = ensure_instrument(session, symbol)
        stored = repository.open_times(session, instrument_id, timeframe, start, end)
        ranges = missing_ranges(stored, start, end, step)
        if refresh_tail_since is not None:
            tail_start = max(start, refresh_tail_since.astimezone(UTC))
            tail = missing_ranges(set(), tail_start, end, step)
            ranges = _merge_ranges(ranges + tail)
        for rs, re_ in ranges:
            chunk_start = rs
            while chunk_start < re_:
                chunk_end = min(re_, chunk_start + max_bars_per_request * step)
                fetched = fetch_fn(symbol, timeframe, chunk_start, chunk_end)
                kept = [c for c in fetched
                        if start <= c.open_time < end and c.open_time + step <= end]
                if kept:
                    repository.upsert_many(session, instrument_id, kept)
                if len(fetched) < max_bars_per_request:
                    break
                chunk_start = chunk_end
        return repository.list_range(session, instrument_id, timeframe, start, end)
    finally:
        try:
            session.close()
        except Exception:
            pass


def _merge_ranges(
    ranges: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged = [ordered[0]]
    for rs, re_ in ordered[1:]:
        ls, le_ = merged[-1]
        if rs <= le_:
            merged[-1] = (ls, max(le_, re_))
        else:
            merged.append((rs, re_))
    return merged
