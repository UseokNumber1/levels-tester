"""HourBounce-кэш: свечи M5/M1 (переиспользует CandleRow) + готовые реплеи (HbReviewRow).

Свечи истории неизменны (кроме формирующейся), поэтому кеш валиден всегда;
`refresh=True` принудительно перечитывает диапазон с Binance. Результаты
привязаны к отпечатку конфига движка + ТФ (CONFIG_FP) — смена параметров
или ТФ автоматически инвалидирует старые строки.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from level_tester.backtester.hourbounce import (
    TF_STEPS_MIN,
    config_for_tf,
    validate_tf,
)
from level_tester.domain.models import Candle
from level_tester.infrastructure.database import HbReviewRow, InstrumentRow

logger = logging.getLogger(__name__)

TF = "5m"  # ТФ по умолчанию (обратная совместимость)
STEP = timedelta(minutes=5)


def step_for_tf(tf: str) -> timedelta:
    return timedelta(minutes=TF_STEPS_MIN[validate_tf(tf)])


def config_fingerprint(tf: str = "5m", exec_mode: str = "grid") -> str:
    """Отпечаток конфига движка + ТФ + режим: реплеи M1/M5 и grid/grid_be не смешиваются."""
    from level_tester.backtester.hourbounce import GRID_BE_LOCK_PCT, GRID_BE_TRIGGER_PCT

    cfg = config_for_tf(validate_tf(tf))
    raw = "|".join([
        tf,
        exec_mode,
        ",".join(str(s) for s in cfg.sl_sizes),
        str(cfg.tp_trail_activate_pct),
        str(cfg.tp_trail_distance_pct),
        str(cfg.confirm_window_w),
        str(cfg.life_window_t),
        str(GRID_BE_TRIGGER_PCT),
        str(GRID_BE_LOCK_PCT),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


CONFIG_FP = config_fingerprint("5m")


def _floor_5m(dt: datetime) -> datetime:
    return _floor_tf("5m", dt)


def _floor_tf(tf: str, dt: datetime) -> datetime:
    step = TF_STEPS_MIN[validate_tf(tf)]
    dt = dt.astimezone(UTC)
    return dt.replace(minute=(dt.minute // step) * step, second=0, microsecond=0)


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


def load_cached(session, instrument_id: int, start: datetime, end: datetime,
                  tf: str = "5m") -> list[Candle]:
    from level_tester.infrastructure.database import CandleRow

    tf = validate_tf(tf)
    rows = session.scalars(
        select(CandleRow)
        .where(
            CandleRow.instrument_id == instrument_id,
            CandleRow.timeframe == tf,
            CandleRow.open_time >= start,
            CandleRow.open_time < end,
        )
        .order_by(CandleRow.open_time)
    )
    out = []
    for r in rows:
        ot = r.open_time.replace(tzinfo=UTC) if r.open_time.tzinfo is None else r.open_time
        ct = r.close_time.replace(tzinfo=UTC) if r.close_time.tzinfo is None else r.close_time
        out.append(Candle(ot, ct, r.open, r.high, r.low, r.close, r.volume, tf))
    return out


def missing_ranges(present: set[datetime], start: datetime, end: datetime,
                   tf: str = "5m") -> list[tuple[datetime, datetime]]:
    """Дыры в сетке ТФ [start, end) -> список (gap_start, gap_end) для догрузки."""
    tf = validate_tf(tf)
    step = step_for_tf(tf)
    start = _floor_tf(tf, start)
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
        t += step
    if cur is not None:
        gaps.append((cur, end))
    return gaps


def upsert_candles(session, instrument_id: int, candles: list[Candle],
                   tf: str = "5m") -> int:
    from level_tester.infrastructure.database import CandleRow

    tf = validate_tf(tf)
    n = 0
    for c in candles:
        row = session.scalar(
            select(CandleRow).where(
                CandleRow.instrument_id == instrument_id,
                CandleRow.timeframe == tf,
                CandleRow.open_time == c.open_time,
            )
        )
        if row is None:
            row = CandleRow(
                instrument_id=instrument_id, timeframe=tf, open_time=c.open_time,
            )
            session.add(row)
        row.close_time = c.close_time
        row.open, row.high, row.low, row.close, row.volume = c.open, c.high, c.low, c.close, c.volume
        n += 1
    return n


def load_candles_cached(session_factory, client, symbol: str, start: datetime, end: datetime,
                        refresh: bool = False, tf: str = "5m") -> tuple[list[Candle], dict[str, Any]]:
    """Свечи ТФ: сначала БД, дыры — с Binance (идемпотентно). Возвращает (свечи, stats)."""
    tf = validate_tf(tf)
    stats: dict[str, Any] = {"from_cache": 0, "from_binance": 0, "refreshed": refresh, "tf": tf}
    try:
        with session_factory() as session:
            iid = ensure_instrument_id(session, symbol)
            if refresh:
                cached: list[Candle] = []
            else:
                cached = load_cached(session, iid, start, end, tf)
            stats["from_cache"] = len(cached)
            present = {c.open_time for c in cached}
            gaps = [(_floor_tf(tf, start), end)] if refresh else missing_ranges(present, start, end, tf)
            fresh: list[Candle] = []
            for gs, ge in gaps:
                # Догрузка дыр с ретраями: без них массовые прогоны (отчёты) под
                # rate-limit Binance молча оставляют дыры в кеше, а T1M на дырявых
                # M1 даёт ложные NO_ENTRY, которые потом оседают в hb_reviews.
                for attempt in range(3):
                    try:
                        fresh.extend(client.klines(symbol, tf, gs, ge))
                        break
                    except Exception:
                        if attempt < 2:
                            logger.debug(
                                "hourbounce candle gap fetch failed (%s %s %s-%s), retry %d",
                                symbol, tf, gs, ge, attempt + 1, exc_info=True)
                            time.sleep(1 + attempt)
                            continue
                        logger.warning(
                            "hourbounce candle gap fetch failed after retries (%s %s %s-%s), "
                            "hole left in cache", symbol, tf, gs, ge, exc_info=True)
            if fresh:
                upsert_candles(session, iid, fresh, tf)
                session.commit()
                stats["from_binance"] = len(fresh)
            if refresh or fresh:
                cached = load_cached(session, iid, start, end, tf)
            cached.sort(key=lambda c: c.open_time)
            return cached, stats
    except Exception:  # noqa: BLE001 - без БД работаем напрямую с Binance, как раньше
        # БД недоступна — напрямую с Binance, как раньше
        return client.klines(symbol, tf, start, end), {"from_cache": 0, "from_binance": -1, "refreshed": refresh, "tf": tf}


def get_review_payload(session, signal_id: str, lookforward: int,
                       tf: str = "5m", exec_mode: str = "grid") -> dict[str, Any] | None:
    fp = config_fingerprint(validate_tf(tf), exec_mode)
    row = session.scalar(
        select(HbReviewRow).where(
            HbReviewRow.signal_id == signal_id,
            HbReviewRow.config_fp == fp,
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
                       payload_cells: list[dict], candles_from: datetime, candles_to: datetime,
                       tf: str = "5m", exec_mode: str = "grid") -> None:
    fp = config_fingerprint(validate_tf(tf), exec_mode)
    row = session.scalar(
        select(HbReviewRow).where(
            HbReviewRow.signal_id == signal_id,
            HbReviewRow.config_fp == fp,
            HbReviewRow.lookforward == lookforward,
        )
    )
    blob = json.dumps(payload_cells, ensure_ascii=False)
    now = datetime.now(UTC)
    if row is None:
        row = HbReviewRow(
            signal_id=signal_id, symbol=symbol.upper(), config_fp=fp,
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
