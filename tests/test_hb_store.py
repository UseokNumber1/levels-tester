"""Кеш HourBounce: дыры в сетке, roundtrip свечей и результатов (sqlite)."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from level_tester.domain.models import Candle
from level_tester.infrastructure.database import create_session_factory, ensure_schema
from level_tester.infrastructure.hourbounce_store import (
    CONFIG_FP,
    get_review_payload,
    load_cached,
    missing_ranges,
    put_review_payload,
    upsert_candles,
)

BASE = datetime(2026, 9, 10, 0, 0, tzinfo=UTC)


def mk_candles(n, t0=BASE):
    out = []
    for i in range(n):
        ot = t0 + i * timedelta(minutes=5)
        out.append(Candle(ot, ot + timedelta(minutes=5), Decimal(100), Decimal(101),
                          Decimal(99), Decimal("100.5"), Decimal(10), "5m"))
    return out


def mem_factory(tmp_path):
    f = create_session_factory(f"sqlite:///{tmp_path}/hb_test.db")
    ensure_schema(f)
    return f


def test_missing_ranges_empty_and_full():
    start, end = BASE, BASE + timedelta(minutes=30)  # 6 бар
    assert missing_ranges(set(), start, end) == [(start, end)]
    present = {BASE + i * timedelta(minutes=5) for i in range(6)}
    assert missing_ranges(present, start, end) == []


def test_missing_ranges_hole_in_middle():
    start, end = BASE, BASE + timedelta(minutes=30)
    present = {BASE + i * timedelta(minutes=5) for i in [0, 1, 4, 5]}
    assert missing_ranges(present, start, end) == [
        (BASE + timedelta(minutes=10), BASE + timedelta(minutes=20))
    ]


def test_candles_roundtrip(tmp_path):
    f = mem_factory(tmp_path)
    with f() as s:
        from level_tester.infrastructure.hourbounce_store import ensure_instrument_id as ei
        iid = ei(s, "TESTUSDT")
        assert upsert_candles(s, iid, mk_candles(10)) == 10
        s.commit()
    with f() as s:
        from level_tester.infrastructure.hourbounce_store import ensure_instrument_id as ei
        iid = ei(s, "TESTUSDT")
        got = load_cached(s, iid, BASE, BASE + timedelta(minutes=50))
        assert len(got) == 10
        assert got[0].open_time == BASE
        assert all(c.timeframe == "5m" for c in got)


def test_review_payload_roundtrip(tmp_path):
    f = mem_factory(tmp_path)
    cells = [{"entry": "T1", "sl_index": 1, "outcome": "TAKE"}]
    with f() as s:
        assert get_review_payload(s, "sig1", 1000) is None
        put_review_payload(s, "sig1", "SEIUSDT", 1000, {"cells": cells}, BASE, BASE + timedelta(hours=1))
    with f() as s:
        hit = get_review_payload(s, "sig1", 1000)
        assert hit is not None and hit["cells"] == cells
        assert CONFIG_FP and len(CONFIG_FP) == 64
