"""TM1 live: equivalence of the extracted cell function + KPI math (no network)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from level_tester.api.app import (
    TM1_COLUMN,
    _tm1_build_rows,
    _tm1_compute_cell,
    _tm1_kpi,
    _tm1_normalize_params,
)
from level_tester.backtester.hourbounce import config_for_tf, review_signal, tm1_exec
from level_tester.domain.models import Candle

BASE = datetime(2025, 1, 1, tzinfo=UTC)


def _mk(rows: list[tuple[float, float, float, float]]) -> list[Candle]:
    return [
        Candle(
            open_time=BASE + i * timedelta(minutes=1),
            close_time=BASE + (i + 1) * timedelta(minutes=1),
            open=Decimal(str(o)), high=Decimal(str(h)),
            low=Decimal(str(lo)), close=Decimal(str(c)),
            volume=Decimal("1"), timeframe="1m", is_closed=True,
        )
        for i, (o, h, lo, c) in enumerate(rows)
    ]


def test_normalize_pairs() -> None:
    p = _tm1_normalize_params({
        "sl_pct": 1.0, "tp_pct": 0, "trail_activation_pct": 1.0,
        "trail_distance_pct": None, "be_trigger_pct": "", "be_lock_pct": 0.1,
        "partial_trigger_pct": 0.5, "partial_close_pct": 50.0,
    })
    assert p["trail_activation_pct"] is None  # пара неполная -> оба выкл
    assert p["trail_distance_pct"] is None
    assert p["be_trigger_pct"] is None
    assert p["tp_pct"] is None


def test_compute_cell_matches_direct_engine() -> None:
    m1 = _mk([
        (101, 101.2, 100.9, 101.0), (100.5, 100.8, 99.9, 100.2),
        (100.2, 101.5, 100.1, 101.4), (101.4, 102.5, 101.3, 102.4),
        (102.4, 102.6, 101.0, 101.2),
    ])
    params = {"sl_pct": 1.0, "tp_pct": 2.0, "trail_activation_pct": 1.0,
              "trail_distance_pct": 0.5, "be_trigger_pct": 0.9, "be_lock_pct": 0.1,
              "partial_trigger_pct": 0.5, "partial_close_pct": 50.0}
    cfg = config_for_tf("1m")
    cell = _tm1_compute_cell("LONG", Decimal("100"), BASE, m1, params, cfg)
    assert cell["outcome"] in ("TAKE", "STOP", "EXPIRED", "NO_ENTRY")
    assert cell["has_partial"] is True
    assert cell["has_be"] is True
    # Прямой прогон движка тем же ExecParams обязан дать тот же исход/цены.
    ex = tm1_exec(sl_pct=1.0, tp_pct=2.0, be_trigger_pct=0.9, be_lock_pct=0.1,
                  trail_activation_pct=1.0, trail_distance_pct=0.5,
                  partial_trigger_pct=0.5, partial_close_pct=50.0)
    direct = review_signal(side="LONG", level_price=Decimal("100"), signal_time=BASE,
                           candles=m1, entry_code="T1M", sl_index=1, config=cfg,
                           exec_params=ex, m1_candles=m1, tf="1m")
    assert Decimal(str(direct.entry_price)) == Decimal(str(cell["entry_price"]))
    assert Decimal(str(direct.exit_price)) == Decimal(str(cell["exit_price"]))
    assert direct.outcome == cell["outcome"]


def test_zero_level_never_crashes() -> None:
    """Битый уровень (entry_price=0.0 из архива): все входы — NO_ENTRY/bad_level."""
    from level_tester.backtester.hourbounce import review_matrix, review_signal

    m1 = _mk([
        (101, 101.2, 100.9, 101.0), (100.5, 100.8, 99.9, 100.2),
        (100.2, 101.5, 100.1, 101.4), (101.4, 102.5, 101.3, 102.4),
    ])
    for code in ("T1", "T2", "T3", "T1M", "T1L"):
        r = review_signal(side="SHORT", level_price=Decimal("0"), signal_time=BASE,
                          candles=m1, entry_code=code, sl_index=1,
                          m1_candles=m1, tf="1m")
        assert r.outcome == "NO_ENTRY" and r.reason == "bad_level"
    # Матрица целиком не падает (кейс XLM: раньше T1L ронял всё с DivisionUndefined).
    res = review_matrix(side="SHORT", level_price=Decimal("0"), signal_time=BASE,
                        candles=m1, exec_mode="grid", m1_candles=m1, tf="1m")
    assert len(res) == 12
    assert all(r.outcome == "NO_ENTRY" and r.reason == "bad_level" for r in res)


def test_build_rows_keeps_meta_and_error() -> None:
    """Ошибка prepare не маскируется и не даёт '?': symbol/дата + текст ошибки."""
    rows, _footer = _tm1_build_rows([{
        "signal_id": "bad1", "symbol": "XLMUSDT", "side": "SHORT", "level_price": "0.2006",
        "dt_place": "2026-09-03 16:05:22", "sig_time_iso": None,
        "created_at": None, "touch_ref": None, "created_ts": None, "worked_ts": None,
        "arch_status": "closed_be_filled", "arch_pnl": 0.5, "traded": True,
        "candles": [], "ncandles": 0, "error": "404: signal not found in archive",
    }], {"sl_pct": 1.0, "tp_pct": None, "trail_activation_pct": 1.0,
          "trail_distance_pct": 1.0, "be_trigger_pct": None, "be_lock_pct": 0.1,
          "partial_trigger_pct": None, "partial_close_pct": None})
    assert rows[0]["symbol"] == "XLMUSDT"
    assert rows[0]["dt_place"] == "2026-09-03 16:05:22"
    assert "404" in rows[0]["error"]
    assert rows[0]["arch_status"] == "closed_be_filled"


def test_build_rows_empty_candles_is_no_m1() -> None:
    """Свечи не загрузились, но meta есть: честная NO_ENTRY/no_m1 строка с символом."""
    rows, _footer = _tm1_build_rows([{
        "signal_id": "s2", "symbol": "XLMUSDT", "side": "SHORT", "level_price": "0.2006",
        "dt_place": "2026-09-03 16:05:22", "sig_time_iso": BASE.isoformat(),
        "created_at": None, "touch_ref": None, "created_ts": None, "worked_ts": None,
        "arch_status": None, "arch_pnl": None, "traded": False,
        "candles": [], "ncandles": 0, "error": "candle load failed: boom",
    }], {"sl_pct": 1.0, "tp_pct": None, "trail_activation_pct": 1.0,
          "trail_distance_pct": 1.0, "be_trigger_pct": None, "be_lock_pct": 0.1,
          "partial_trigger_pct": None, "partial_close_pct": None})
    assert rows[0]["symbol"] == "XLMUSDT"
    assert rows[0]["cells"][TM1_COLUMN]["outcome"] == "ERROR"
    assert "boom" in rows[0]["error"]
    # Тот же кейс без текста ошибки (легаси-сессия): NO_ENTRY/no_m1 с символом.
    rows2, _ = _tm1_build_rows([{
        "signal_id": "s2", "symbol": "XLMUSDT", "side": "SHORT", "level_price": "0.2006",
        "dt_place": "2026-09-03 16:05:22", "sig_time_iso": BASE.isoformat(),
        "created_at": None, "touch_ref": None, "created_ts": None, "worked_ts": None,
        "arch_status": None, "arch_pnl": None, "traded": False,
        "candles": [], "ncandles": 0,
    }], {"sl_pct": 1.0, "tp_pct": None, "trail_activation_pct": 1.0,
          "trail_distance_pct": 1.0, "be_trigger_pct": None, "be_lock_pct": 0.1,
          "partial_trigger_pct": None, "partial_close_pct": None})
    assert rows2[0]["symbol"] == "XLMUSDT"
    assert rows2[0]["cells"][TM1_COLUMN]["outcome"] == "NO_ENTRY"
    assert "error" not in rows2[0]


def test_recalc_carries_detail_fields() -> None:
    from fastapi.testclient import TestClient

    from level_tester.api import app as _app

    c = TestClient(_app.app)
    _app._TM1_SESS.pop("t-test", None)
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    m1 = _mk([(101, 101.2, 100.9, 101.0), (100.5, 100.8, 99.9, 100.2),
              (100.2, 101.5, 100.1, 101.4)])
    _app._TM1_SESS["t-test"] = {"created": _dt.now(_UTC), "items": [{
        "signal_id": "s0", "symbol": "BTCUSDT", "side": "LONG", "level_price": "100",
        "dt_place": "2025-01-01 00:00:00", "sig_time_iso": BASE.isoformat(),
        "created_at": None, "touch_ref": None, "created_ts": None, "worked_ts": None,
        "arch_status": None, "arch_pnl": None, "traded": False,
        "candles": m1, "ncandles": len(m1)}], "lookforward": 10000}
    try:
        r = c.post("/api/hourbounce/report-tm1/recalc", json={
            "session_id": "t-test", "sl_pct": 1.0, "tp_pct": None,
            "trail_activation_pct": 1.0, "trail_distance_pct": 1.0,
            "be_trigger_pct": None, "be_lock_pct": 0.1,
            "partial_trigger_pct": None, "partial_close_pct": None})
        assert r.status_code == 200
        cell = r.json()["rows"][0]["cells"]["TM1"]
        for k in ("sl_price", "gross_pnl_pct", "fee_pct", "bars_in_trade"):
            assert k in cell, k
        assert "mean" in r.json()["kpi"] and "median" in r.json()["kpi"]
        assert len(r.json()["kpi"]["equity"]) == r.json()["count"]
    finally:
        _app._TM1_SESS.pop("t-test", None)


def test_build_rows_and_kpi() -> None:
    m1 = _mk([
        (101, 101.2, 100.9, 101.0), (100.5, 100.8, 99.9, 100.2),
        (100.2, 101.5, 100.1, 101.4),
    ])
    items = [{
        "signal_id": "s1", "symbol": "BTCUSDT", "side": "LONG", "level_price": "100",
        "dt_place": "2025-01-01 00:00:00", "sig_time_iso": BASE.isoformat(),
        "created_at": None, "touch_ref": None, "created_ts": None, "worked_ts": None,
        "arch_status": None, "arch_pnl": None, "traded": False, "candles": m1,
    }]
    params = {"sl_pct": 1.0, "tp_pct": None, "trail_activation_pct": 1.0,
              "trail_distance_pct": 1.0, "be_trigger_pct": None, "be_lock_pct": 0.1,
              "partial_trigger_pct": None, "partial_close_pct": None}
    rows, footer = _tm1_build_rows(items, params)
    assert len(rows) == 1
    assert TM1_COLUMN in rows[0]["cells"]
    assert footer["TOTAL"] == round(rows[0]["best_pnl"], 2)
    kpi = _tm1_kpi(rows)
    assert kpi["count"] == 1
    assert kpi["total"] == footer["TOTAL"]
    assert len(kpi["equity"]) == 1
    assert isinstance(kpi["hist"], dict) and "counts" in kpi["hist"]
