"""HourBounce-кэш: свечи M5 (переиспользует CandleRow) + готовые реплеи (HbReviewRow).

Свечи истории неизменны (кроме формирующейся), поэтому кеш валиден всегда;
`refresh=True` принудительно перечитывает диапазон с Binance. Результаты
привязаны к отпечатку конфига движка (CONFIG_FP) — смена параметров
автоматически инвалидирует старые строки.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from level_tester.backtester.hourbounce import DEFAULT_CONFIG
from level_tester.domain.models import Candle
from level_tester.infrastructure.database import HbReviewRow, InstrumentRow

logger = logging.getLogger(__name__)

TF = "5m"
STEP = timedelta(minutes=5)


def config_fingerprint() -> str:
    raw = "|".join([
        ",".join(str(s) for s in DEFAULT_CONFIG.sl_sizes),
        str(DEFAULT_CONFIG.tp_trail_activate_pct),
        str(DEFAULT_CONFIG.tp_trail_distance_pct),
        str(DEFAULT_CONFIG.confirm_window_w),
        str(DEFAULT_CONFIG.life_window_t),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


CONFIG_FP = config_fingerprint()


def _floor_5m(dt: datetime) -> datetime:
    dt = dt.astimezone(UTC)
    return dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)


def ensure_instrument_id(session, symbol: str) -> int:
    symbol = symbol.upper()
    row = session.scalar(select(InstrumentRow).where(InstrumentRow.symbol == symbol))
    if row is None:
        row = InstrumentRow(
            symbol=symbol, exchange="binance", market_type="usdt_m_futures",
            quote_asset="USDT", base_asset=symbol.removesuffix("USDT"), status="TRADING",
            daily_volume=Decimal(0),
        )
        session.add(row)
        session.flush()
    return row.id


def load_cached(session, instrument_id: int, start: datetime, end: datetime) -> list[Candle]:
    from level_tester.infrastructure.database import CandleRow

    rows = session.scalars(
        select(CandleRow)
        .where(
            CandleRow.instrument_id == instrument_id,
            CandleRow.timeframe == TF,
            CandleRow.open_time >= start,
            CandleRow.open_time < end,
        )
        .order_by(CandleRow.open_time)
    )
    out = []
    for r in rows:
        ot = r.open_time.replace(tzinfo=UTC) if r.open_time.tzinfo is None else r.open_time
        ct = r.close_time.replace(tzinfo=UTC) if r.close_time.tzinfo is None else r.close_time
        out.append(Candle(ot, ct, r.open, r.high, r.low, r.close, r.volume, TF))
    return out


def missing_ranges(present: set[datetime], start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Дыры в 5m-сетке [start, end) -> список (gap_start, gap_end) для догрузки."""
    start = _floor_5m(start)
    gaps: list[tuple[datetime, datetime]] = []
    cur: datetime | None = None
    t = start
    while t < end:
        if t not in present:
            if cur is None:
                cur = t
        elif cur is not None:
            gaps.append((cur, t))
            cur = None
        t += STEP
    if cur is not None:
        gaps.append((cur, end))
    return gaps


def upsert_candles(session, instrument_id: int, candles: list[Candle]) -> int:
    from level_tester.infrastructure.database import CandleRow

    n = 0
    for c in candles:
        row = session.scalar(
            select(CandleRow).where(
                CandleRow.instrument_id == instrument_id,
                CandleRow.timeframe == TF,
                CandleRow.open_time == c.open_time,
            )
        )
        if row is None:
            row = CandleRow(
                instrument_id=instrument_id, timeframe=TF, open_time=c.open_time,
            )
            session.add(row)
        row.close_time = c.close_time
        row.open, row.high, row.low, row.close, row.volume = c.open, c.high, c.low, c.close, c.volume
        n += 1
    return n


def load_candles_cached(session_factory, client, symbol: str, start: datetime, end: datetime,
                        refresh: bool = False) -> tuple[list[Candle], dict[str, Any]]:
    """Свечи M5: сначала БД, дыры — с Binance (идемпотентно). Возвращает (свечи, stats)."""
    stats: dict[str, Any] = {"from_cache": 0, "from_binance": 0, "refreshed": refresh}
    try:
        with session_factory() as session:
            iid = ensure_instrument_id(session, symbol)
            if refresh:
                cached: list[Candle] = []
            else:
                cached = load_cached(session, iid, start, end)
            stats["from_cache"] = len(cached)
            present = {c.open_time for c in cached}
            gaps = [(_floor_5m(start), end)] if refresh else missing_ranges(present, start, end)
            fresh: list[Candle] = []
            for gs, ge in gaps:
                try:
                    fresh.extend(client.klines(symbol, TF, gs, ge))
                except Exception:
                    logger.debug("hourbounce candle gap fetch failed", exc_info=True)
                    continue
            if fresh:
                upsert_candles(session, iid, fresh)
                session.commit()
                stats["from_binance"] = len(fresh)
            if refresh or fresh:
                cached = load_cached(session, iid, start, end)
            cached.sort(key=lambda c: c.open_time)
            return cached, stats
    except Exception:  # noqa: BLE001 - без БД работаем напрямую с Binance, как раньше
        # БД недоступна — напрямую с Binance, как раньше
        return client.klines(symbol, TF, start, end), {"from_cache": 0, "from_binance": -1, "refreshed": refresh}


def get_review_payload(session, signal_id: str, lookforward: int) -> dict[str, Any] | None:
    row = session.scalar(
        select(HbReviewRow).where(
            HbReviewRow.signal_id == signal_id,
            HbReviewRow.config_fp == CONFIG_FP,
            HbReviewRow.lookforward == lookforward,
        )
    )
    if row is None:
        return None
    try:
        data = json.loads(row.payload_json)
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


def put_review_payload(session, signal_id: str, symbol: str, lookforward: int,
                       payload_cells: list[dict], candles_from: datetime, candles_to: datetime) -> None:
    row = session.scalar(
        select(HbReviewRow).where(
            HbReviewRow.signal_id == signal_id,
            HbReviewRow.config_fp == CONFIG_FP,
            HbReviewRow.lookforward == lookforward,
        )
    )
    blob = json.dumps(payload_cells, ensure_ascii=False)
    now = datetime.now(UTC)
    if row is None:
        row = HbReviewRow(
            signal_id=signal_id, symbol=symbol.upper(), config_fp=CONFIG_FP,
            lookforward=lookforward, payload_json=blob,
            candles_from=candles_from, candles_to=candles_to, updated_at=now,
        )
        session.add(row)
    else:
        row.payload_json = blob
        row.candles_from = candles_from
        row.candles_to = candles_to
        row.updated_at = now
    session.commit()
