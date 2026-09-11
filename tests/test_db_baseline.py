"""Parity tests: DB baseline variant resolves like P_G_V2 and trades like the engine."""
from __future__ import annotations

from datetime import datetime, UTC, timedelta
from decimal import Decimal

from level_tester.backtester.db_variant import DB_VARIANT_ID, resolve_db_variant
from level_tester.backtester.signal_reader import SignalEntry
from level_tester.backtester.trade_model import BacktestTrade


def _sig(**kw) -> SignalEntry:
    base = dict(
        signal_id="s1", symbol="BTCUSDT", side="LONG", entry_price=100.0,
        stop_loss=99.0, rr_ratio=3.0, timeframe="1h",
        timestamp="2026-08-01T00:00:00", source="trading",
    )
    base.update(kw)
    return SignalEntry(**base)


def test_db_resolve_uses_snapshot() -> None:
    s = _sig(
        breakeven_enabled=True, breakeven_trigger_pct=0.9, breakeven_profit_pct=0.35,
        breakeven_fix_enabled=True, breakeven_fix_pct=50.0,
        trailing_stop_enabled=True, trailing_activation_pct=1.0,
        trailing_stop_pct=0.6, trailing_update_threshold_pct=0.5,
        trailing_tp_only=False, take_profits="[103.0]",
    )
    d = resolve_db_variant(s, live={})
    v = d.variant
    assert v.id == DB_VARIANT_ID
    assert v.sl_pct == Decimal("1")
    assert v.tp_pct == Decimal("3")
    assert v.breakeven_trigger_pct == Decimal("0.9")
    assert v.trailing_stop_pct == Decimal("0.6")
    # fix at BE trigger level expressed as RR = trigger / sl
    assert v.partial_close_pct == Decimal("50")
    assert v.partial_close_rr == Decimal("0.9")
    assert d.sl_price == 99.0
    assert d.tp_price == 103.0


def test_db_resolve_live_fallback() -> None:
    s = _sig(stop_loss=None, take_profits=None, rr_ratio=None,
             breakeven_enabled=None, breakeven_trigger_pct=None,
             breakeven_profit_pct=None, breakeven_fix_enabled=None,
             breakeven_fix_pct=None, trailing_stop_enabled=None,
             trailing_activation_pct=None, trailing_stop_pct=None,
             trailing_update_threshold_pct=None, trailing_tp_only=None,
             source="archive")
    live = {
        "stop_loss_pct": 1.0, "take_profit_method": "rr_ratio", "rr_ratio": 3.0,
        "breakeven_enabled": True, "breakeven_trigger_pct": 0.9,
        "breakeven_profit_pct": 0.35, "breakeven_fix_enabled": True,
        "breakeven_fix_pct": 50.0, "trailing_stop_enabled": True,
        "trailing_activation_pct": 1.0, "trailing_stop_pct": 0.6,
        "trailing_update_threshold_pct": 0.5, "trailing_tp_only": False,
    }
    d = resolve_db_variant(s, live=live)
    assert d.sources["sl"] == "live"
    assert d.variant.sl_pct == Decimal("1")
    assert d.variant.tp_rr == Decimal("3")
    assert d.sl_price == 99.0


def test_trailing_waits_for_be() -> None:
    now = datetime.now(UTC)
    t = BacktestTrade(id="x", side="LONG", entry_time=now,
                      entry_price=Decimal("100"), stop_price=Decimal("99"),
                      take_price=Decimal("103"))
    t.breakeven_trigger_pct = Decimal("0.9")
    t.breakeven_lock_pct = Decimal("0.35")
    t.trailing_activation_pct = Decimal("1.0")
    t.trailing_stop_pct = Decimal("0.6")
    t.trailing_update_threshold_pct = Decimal("0.5")
    # +1.05% triggers BE (0.9) and would trigger trailing (1.0) — prod order: no trail same tick
    t.tick(Decimal("101.05"), Decimal("100.5"), Decimal("100.9"), now, 1)
    assert t.breakeven_active is True
    assert t.trailing_activated is False
    # next tick above activation -> trailing engages
    t.tick(Decimal("101.3"), Decimal("100.9"), Decimal("101.1"), now, 2)
    assert t.trailing_activated is True


def test_trailing_threshold_price_based() -> None:
    now = datetime.now(UTC)
    t = BacktestTrade(id="x", side="LONG", entry_time=now,
                      entry_price=Decimal("100"), stop_price=Decimal("99"),
                      take_price=Decimal("110"))
    t.trailing_activation_pct = Decimal("1.0")
    t.trailing_stop_pct = Decimal("0.6")
    t.trailing_update_threshold_pct = Decimal("0.5")
    t.tick(Decimal("101.2"), Decimal("100.7"), Decimal("101.0"), now, 1)
    first_stop = t.trailing_current_stop
    assert first_stop is not None
    # +0.1% move < 0.5% threshold -> no update
    t.tick(Decimal("101.3"), Decimal("100.7"), Decimal("101.1"), now, 2)
    assert t.trailing_current_stop == first_stop
    # +1% move -> update
    t.tick(Decimal("102.4"), Decimal("101.8"), Decimal("102.0"), now, 3)
    assert t.trailing_current_stop > first_stop


def test_partial_weighted_pnl() -> None:
    now = datetime.now(UTC)
    t = BacktestTrade(id="y", side="LONG", entry_time=now,
                      entry_price=Decimal("100"), stop_price=Decimal("99"),
                      take_price=Decimal("103"))
    t.partial_close_pct = Decimal("50")
    t.partial_close_rr = Decimal("0.9")  # fix 50% @ +0.9%
    t.breakeven_lock_pct = Decimal("0.35")
    # fix triggers, remainder stopped at BE 100.35 same candle (low 100.2)
    t.tick(Decimal("101.0"), Decimal("100.2"), Decimal("100.9"), now, 1)
    assert t.status == "closed"
    # 0.5 * 0.9 + 0.5 * 0.35 = 0.625
    assert abs(float(t.pnl_pct) - 0.625) < 1e-6


def test_partial_then_tp_weighted() -> None:
    t0 = datetime.now(UTC)
    t = BacktestTrade(id="z", side="LONG", entry_time=t0,
                      entry_price=Decimal("100"), stop_price=Decimal("99"),
                      take_price=Decimal("103"))
    t.partial_close_pct = Decimal("50")
    t.partial_close_rr = Decimal("0.9")
    t.breakeven_lock_pct = Decimal("0.35")
    # candle 1: fix at 100.9, low stays above new BE stop
    closed = t.tick(Decimal("101.0"), Decimal("100.5"), Decimal("100.9"), t0, 1)
    assert closed is False
    assert t.partial_closed is True
    # candle 2: TP at 103
    closed = t.tick(Decimal("103.5"), Decimal("102.0"), Decimal("103.0"),
                    t0 + timedelta(minutes=5), 2)
    assert closed is True
    # 0.5 * 0.9 + 0.5 * 3.0 = 1.95
    assert abs(float(t.pnl_pct) - 1.95) < 1e-6
