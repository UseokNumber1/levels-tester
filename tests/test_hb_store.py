"""Кеш HourBounce: дыры в сетке, roundtrip свечей и результатов (sqlite)."""
import pytest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from level_tester.backtester.hourbounce import config_for_tf, step_for_tf, validate_tf
from level_tester.domain.models import Candle
from level_tester.infrastructure.database import create_session_factory, ensure_schema
from level_tester.infrastructure.hourbounce_store import (
    CONFIG_FP,
    config_fingerprint,
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


def test_validate_tf_rejects_unknown():
    with pytest.raises(ValueError):
        validate_tf("15m")
    assert step_for_tf("5m") == 5 and step_for_tf("1m") == 1


def test_config_for_tf_windows_cover_same_time():
    c5, c1 = config_for_tf("5m"), config_for_tf("1m")
    assert c5.confirm_window_w == 20
    assert c1.confirm_window_w == 100
    assert c1.confirm_window_w * 1 == c5.confirm_window_w * 5 == 100  # минут
    assert config_fingerprint("5m") != config_fingerprint("1m")


def test_missing_ranges_1m():
    start, end = BASE, BASE + timedelta(minutes=5)  # 5 бар M1
    present = {BASE + i * timedelta(minutes=1) for i in [0, 1, 4]}
    assert missing_ranges(present, start, end, "1m") == [
        (BASE + timedelta(minutes=2), BASE + timedelta(minutes=4))
    ]
    assert missing_ranges(set(), start, end, "1m") == [(start, end)]


def mk_candles_tf(n, tf, t0=BASE):
    step = 5 if tf == "5m" else 1
    out = []
    for i in range(n):
        ot = t0 + i * timedelta(minutes=step)
        out.append(Candle(ot, ot + timedelta(minutes=step), Decimal(100), Decimal(101),
                          Decimal(99), Decimal("100.5"), Decimal(10), tf))
    return out


def test_candles_namespaced_by_tf(tmp_path):
    f = mem_factory(tmp_path)
    with f() as s:
        from level_tester.infrastructure.hourbounce_store import ensure_instrument_id as ei
        iid = ei(s, "TESTUSDT")
        assert upsert_candles(s, iid, mk_candles_tf(10, "5m"), "5m") == 10
        assert upsert_candles(s, iid, mk_candles_tf(50, "1m"), "1m") == 50
        s.commit()
    with f() as s:
        from level_tester.infrastructure.hourbounce_store import ensure_instrument_id as ei
        iid = ei(s, "TESTUSDT")
        got5 = load_cached(s, iid, BASE, BASE + timedelta(minutes=50), "5m")
        got1 = load_cached(s, iid, BASE, BASE + timedelta(minutes=50), "1m")
        assert len(got5) == 10 and all(c.timeframe == "5m" for c in got5)
        assert len(got1) == 50 and all(c.timeframe == "1m" for c in got1)


def test_review_payload_namespaced_by_tf(tmp_path):
    f = mem_factory(tmp_path)
    c5 = [{"entry": "T1", "outcome": "TAKE"}]
    c1 = [{"entry": "T1", "outcome": "STOP"}]
    with f() as s:
        put_review_payload(s, "sigX", "SEIUSDT", 2000, {"cells": c5},
                           BASE, BASE + timedelta(hours=1), "5m")
        put_review_payload(s, "sigX", "SEIUSDT", 2000, {"cells": c1},
                           BASE, BASE + timedelta(hours=1), "1m")
    with f() as s:
        assert get_review_payload(s, "sigX", 2000, "5m")["cells"] == c5
        assert get_review_payload(s, "sigX", 2000, "1m")["cells"] == c1


def test_review_payload_namespaced_by_exec_mode(tmp_path):
    assert config_fingerprint("5m", "grid") != config_fingerprint("5m", "grid_be")
    f = mem_factory(tmp_path)
    cg = [{"entry": "T1", "outcome": "TAKE"}]
    cb = [{"entry": "T1", "outcome": "STOP"}]
    with f() as s:
        put_review_payload(s, "sigY", "SEIUSDT", 2000, {"cells": cg},
                           BASE, BASE + timedelta(hours=1), "5m", "grid")
        put_review_payload(s, "sigY", "SEIUSDT", 2000, {"cells": cb},
                           BASE, BASE + timedelta(hours=1), "5m", "grid_be")
    with f() as s:
        assert get_review_payload(s, "sigY", 2000, "5m", "grid")["cells"] == cg
        assert get_review_payload(s, "sigY", 2000, "5m", "grid_be")["cells"] == cb
