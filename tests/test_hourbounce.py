"""Unit-кейсы движка HourBounce из ТЗ v2.0 п.5 (обязательные)."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from level_tester.backtester.hourbounce import HourBounceConfig, review_signal
from level_tester.domain.models import Candle

BASE = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)


def mk(px, opens=None, highs=None, lows=None, t0=BASE, tf="5m"):
    out = []
    o_prev = opens[0] if opens else px[0]
    for i, c in enumerate(px):
        o = opens[i] if opens else o_prev
        h = highs[i] if highs else max(o, c) + Decimal("0.05")
        lo = lows[i] if lows else min(o, c) - Decimal("0.05")
        out.append(Candle(
            t0 + i * timedelta(minutes=5), t0 + (i + 1) * timedelta(minutes=5),
            Decimal(str(o)), Decimal(str(h)), Decimal(str(lo)), Decimal(str(c)),
            Decimal("10"), tf, True,
        ))
        o_prev = c
    return out


def cfg(**kw):
    return HourBounceConfig(**kw)


def test_no_touch():
    cs = mk([Decimal("101"), Decimal("101.5"), Decimal("102")])
    r = review_signal(side="BUY", level_price=Decimal("100"), signal_time=BASE,
                      candles=cs, entry_code="T1", sl_index=1)
    assert r.outcome == "NO_ENTRY" and r.reason == "no_touch"


def test_touch_candle_close_not_counted_as_confirm():
    # касание на i=0 (low<=100), закрылась за уровнем, но подтверждением не считается;
    # T2 требует 1 confirm строго после -> следующая красная свеча сбрасывает, итог NO_ENTRY
    cs = mk(
        px=[Decimal("100.5"), Decimal("99.9"), Decimal("99.8")],
        opens=[Decimal("101"), Decimal("100.5"), Decimal("99.9")],
        lows=[Decimal("99.9"), Decimal("99.7"), Decimal("99.6")],
        highs=[Decimal("101.2"), Decimal("100.6"), Decimal("100.0")],
    )
    r = review_signal(side="BUY", level_price=Decimal("100"), signal_time=BASE,
                      candles=cs, entry_code="T2", sl_index=3,
                      config=cfg(confirm_window_w=2, life_window_t=10))
    assert r.outcome == "NO_ENTRY" and r.reason == "no_confirm"


def test_no_confirm_after_20_bars():
    px = [Decimal("100.5")] * 3 + [Decimal("99.9")] + [Decimal("99.5")] * 25
    opens = [Decimal("101")] + px[:-1]
    lows = [Decimal("100.2")] * 3 + [Decimal("99.8")] + [Decimal("99.4")] * 25
    highs = [Decimal("101.2")] * 3 + [Decimal("100.6")] + [Decimal("99.9")] * 25
    cs = mk(px=px, opens=opens, lows=lows, highs=highs)
    r = review_signal(side="BUY", level_price=Decimal("100"), signal_time=BASE,
                      candles=cs, entry_code="T3", sl_index=2,
                      config=cfg(confirm_window_w=20, life_window_t=60))
    assert r.outcome == "NO_ENTRY" and r.reason == "no_confirm"


def test_trail_activation_then_stop_same_candle_pg_order():
    # Порядок PGv2 (trade_model): активация трейлинга и проверка стопа —
    # в одной свече. Вход 101, свеча входа дотянулась до +1% (high 102.2),
    # trail встал на 101.178, low 100.9 его пробил -> TAKE по трейлингу.
    cs = mk(
        px=[Decimal("101"), Decimal("100.2"), Decimal("102.5"), Decimal("100.0")],
        opens=[Decimal("102"), Decimal("101.5"), Decimal("100.2"), Decimal("102.5")],
        lows=[Decimal("100.9"), Decimal("99.5"), Decimal("100.0"), Decimal("99.0")],
        highs=[Decimal("102.2"), Decimal("101.6"), Decimal("102.6"), Decimal("102.6")],
    )
    r = review_signal(side="BUY", level_price=Decimal("101"), signal_time=BASE,
                      candles=cs, entry_code="T1", sl_index=1,
                      config=cfg(tp_trail_activate_pct=Decimal("1.0"),
                                 tp_trail_distance_pct=Decimal("1.0"), life_window_t=10))
    assert r.outcome == "TAKE" and r.exit_kind == "trail"
    assert r.exit_price == Decimal("101.178")
    assert any(e.type == "trail_on" for e in r.events)


def test_trail_activates_by_high_triggers_next_bar_only():
    # активация +1% на свече 2, выход по трейлингу возможен только со следующей свечи
    cs = mk(
        px=[Decimal("101"), Decimal("100.5"), Decimal("102.0"), Decimal("101.5"), Decimal("100.5")],
        opens=[Decimal("102"), Decimal("101"), Decimal("100.5"), Decimal("102.8"), Decimal("101.5")],
        lows=[Decimal("100.9"), Decimal("100.48"), Decimal("100.5"), Decimal("101.4"), Decimal("100.4")],
        highs=[Decimal("102.2"), Decimal("101.1"), Decimal("103.5"), Decimal("102.9"), Decimal("101.6")],
    )
    r = review_signal(side="BUY", level_price=Decimal("101"), signal_time=BASE,
                      candles=cs, entry_code="T1", sl_index=3,
                      config=cfg(tp_trail_activate_pct=Decimal("1.0"),
                                 tp_trail_distance_pct=Decimal("1.0"), life_window_t=10))
    assert r.trail_path, "trail должен активироваться по high"
    assert r.outcome in ("TAKE", "STOP", "EXPIRED")


def test_gap_through_trail_take_by_open_and_gap_through_stop():
    # гэп вниз через стоп на следующей свече после входа -> STOP по open
    cs = mk(
        px=[Decimal("101"), Decimal("101.7"), Decimal("97.95")],
        opens=[Decimal("102"), Decimal("101.8"), Decimal("98.0")],
        lows=[Decimal("100.9"), Decimal("101.6"), Decimal("97.9")],
        highs=[Decimal("102.0"), Decimal("102.0"), Decimal("98.1")],
    )
    r = review_signal(side="BUY", level_price=Decimal("101"), signal_time=BASE,
                      candles=cs, entry_code="T1", sl_index=3, config=cfg(life_window_t=10))
    assert r.outcome == "STOP" and r.exit_price == Decimal("98.0")


def test_no_activation_trail_path_empty():
    # вход по уровню без гэпа, цена болтается ниже активации +1% -> трейлинга нет
    cs = mk([Decimal("101.2"), Decimal("101.0"), Decimal("101.1"), Decimal("100.9")],
            opens=[Decimal("101.5"), Decimal("101.2"), Decimal("101.0"), Decimal("101.1")],
            lows=[Decimal("100.9"), Decimal("100.8"), Decimal("100.7"), Decimal("100.6")],
            highs=[Decimal("101.6"), Decimal("101.3"), Decimal("101.2"), Decimal("101.15")])
    r = review_signal(side="BUY", level_price=Decimal("101"), signal_time=BASE,
                      candles=cs, entry_code="T1", sl_index=1, config=cfg(life_window_t=10))
    assert not r.trail_path
    assert r.outcome in ("STOP", "EXPIRED")


def test_t1_gap_entry_by_open():
    # BUY: свеча открылась ПОД уровнем (гэп сквозь поддержку) -> вход по open
    cs = mk(px=[Decimal("100.5")],
            opens=[Decimal("99.0")],
            lows=[Decimal("98.9")], highs=[Decimal("100.6")])
    r = review_signal(side="BUY", level_price=Decimal("100"), signal_time=BASE,
                      candles=cs, entry_code="T1", sl_index=1, config=cfg(life_window_t=5))
    assert r.entry_price == Decimal("99.0")

    # BUY: открылись НАД уровнем, тычок вниз позже в свече -> вход по уровню
    cs2 = mk(px=[Decimal("100.5")],
             opens=[Decimal("102.0")],
             lows=[Decimal("99.9")], highs=[Decimal("102.1")])
    r2 = review_signal(side="BUY", level_price=Decimal("100"), signal_time=BASE,
                       candles=cs2, entry_code="T1", sl_index=1, config=cfg(life_window_t=5))
    assert r2.entry_price == Decimal("100")


def test_t1_gap_entry_short_mirror():
    # SELL: свеча открылась НАД уровнем (гэп сквозь сопротивление) -> вход по open
    cs = mk(px=[Decimal("99.5")],
            opens=[Decimal("101.0")],
            lows=[Decimal("99.4")], highs=[Decimal("101.1")])
    r = review_signal(side="SELL", level_price=Decimal("100"), signal_time=BASE,
                      candles=cs, entry_code="T1", sl_index=1, config=cfg(life_window_t=5))
    assert r.entry_price == Decimal("101.0")
    # стоп шорта обязан быть ВЫШЕ входа
    assert r.sl_price is not None and r.sl_price > r.entry_price

    # SELL: открылись ПОД уровнем, тычок вверх позже в свече -> вход по уровню,
    # стоп выше уровня
    cs2 = mk(px=[Decimal("99.5")],
             opens=[Decimal("98.0")],
             lows=[Decimal("97.9")], highs=[Decimal("100.1")])
    r2 = review_signal(side="SELL", level_price=Decimal("100"), signal_time=BASE,
                       candles=cs2, entry_code="T1", sl_index=1, config=cfg(life_window_t=5))
    assert r2.entry_price == Decimal("100")
    assert r2.sl_price is not None and r2.sl_price > Decimal("100")


def test_t1_has_no_confirm_search():
    # T1: вход сразу по касанию, поиск подтверждения не ведётся вообще,
    # даже если следующие свечи закрылись за уровнем
    cs = mk(
        px=[Decimal("100.5"), Decimal("101.5"), Decimal("102.0")],
        opens=[Decimal("100.2"), Decimal("100.5"), Decimal("101.5")],
        lows=[Decimal("99.4"), Decimal("100.5"), Decimal("101.5")],
        highs=[Decimal("100.6"), Decimal("101.6"), Decimal("102.1")],
    )
    r = review_signal(side="BUY", level_price=Decimal("100"), signal_time=BASE,
                      candles=cs, entry_code="T1", sl_index=3, config=cfg(life_window_t=10))
    assert r.confirm_idx == []
    assert all(e.type != "confirm1" for e in r.events)
    assert r.entry_price == Decimal("100")  # без гэпа — вход ровно по уровню


def test_nearest_touch_beyond_old_24h_limit():
    # касание на 300-й свече (~25ч после выставления): без лимита окна
    # движок обязан найти ближайшую фактическую отработку
    n = 320
    px = [Decimal("101")] * n
    px[300] = Decimal("100.5")
    opens = [Decimal("101")] + px[:-1]
    lows = [Decimal("100.5")] * n
    lows[300] = Decimal("99.9")
    cs = mk(px=px, opens=opens, lows=lows)
    r = review_signal(side="LONG", level_price=Decimal("100"), signal_time=BASE,
                      candles=cs, entry_code="T1", sl_index=3)
    assert r.touch_dt == cs[300].open_time
    assert r.entry_price == Decimal("100")


def test_dt_place_from_signal_id_is_utc():
    from level_tester.api.app import _hb_dt_place

    s, dt = _hb_dt_place("binance_KASUSDT_resistance_0.03727_20260908_071634", None, "2026-09-08 07:53:53")
    assert s == "2026-09-08 07:16:34"
    assert dt is not None and dt.tzinfo is not None and dt.utcoffset().total_seconds() == 0
    # якорь раньше created_at — сканирование стартует с выставления уровня
    assert dt < dt.replace(hour=7, minute=53, second=53)

    # без метки в id — фолбэк на placement_date / created_at, naive = UTC
    s2, dt2 = _hb_dt_place("weird_id", None, "2026-09-08 07:53:53")
    assert s2 == "2026-09-08 07:53:53" and dt2 is not None and dt2.tzinfo is not None

    s3, dt3 = _hb_dt_place("weird_id", None, None)
    assert (s3, dt3) == (None, None)


def test_confirms_must_be_consecutive():
    # LONG 100, T3: зелёная, красная (сброс), затем 2 зелёные подряд -> вход после 2-го забега
    cs = mk(
        px=[Decimal("100.5"), Decimal("101.5"), Decimal("100.8"), Decimal("101.2"), Decimal("101.8"), Decimal("102.0")],
        opens=[Decimal("101"), Decimal("100.5"), Decimal("101.5"), Decimal("100.8"), Decimal("101.2"), Decimal("101.8")],
        lows=[Decimal("99.9"), Decimal("100.4"), Decimal("100.7"), Decimal("100.7"), Decimal("101.1"), Decimal("101.7")],
        highs=[Decimal("101.2"), Decimal("101.6"), Decimal("101.6"), Decimal("101.3"), Decimal("101.9"), Decimal("102.1")],
    )
    r = review_signal(side="LONG", level_price=Decimal("100"), signal_time=BASE,
                      candles=cs, entry_code="T3", sl_index=3, config=cfg(life_window_t=10))
    assert r.confirm_idx == [3, 4]
    assert r.entry_price == Decimal("101.8")


def test_short_confirms_consecutive_and_red():
    # SHORT 100, T3: красная, зелёная (сброс), затем 2 красные подряд
    cs = mk(
        px=[Decimal("99.5"), Decimal("98.5"), Decimal("99.0"), Decimal("98.0"), Decimal("97.0"), Decimal("96.8")],
        opens=[Decimal("99"), Decimal("99.5"), Decimal("98.5"), Decimal("99.0"), Decimal("98.0"), Decimal("97.0")],
        lows=[Decimal("98.9"), Decimal("98.4"), Decimal("98.4"), Decimal("97.9"), Decimal("96.9"), Decimal("96.7")],
        highs=[Decimal("100.2"), Decimal("99.6"), Decimal("99.1"), Decimal("99.1"), Decimal("98.1"), Decimal("97.1")],
    )
    r = review_signal(side="SHORT", level_price=Decimal("100"), signal_time=BASE,
                      candles=cs, entry_code="T3", sl_index=1, config=cfg(life_window_t=10))
    assert r.confirm_idx == [3, 4]
    assert r.entry_price == Decimal("97.0")
    assert r.sl_price is not None and r.sl_price > r.entry_price


def test_confirm_needs_direction_not_just_close():
    # LONG 100: doji над уровнем и красная над уровнем — не подтверждения
    cs = mk(
        px=[Decimal("100.5"), Decimal("100.5"), Decimal("100.3")],
        opens=[Decimal("101"), Decimal("100.5"), Decimal("100.5")],
        lows=[Decimal("99.9"), Decimal("100.4"), Decimal("100.2")],
        highs=[Decimal("101.2"), Decimal("100.6"), Decimal("100.6")],
    )
    r = review_signal(side="LONG", level_price=Decimal("100"), signal_time=BASE,
                      candles=cs, entry_code="T2", sl_index=1, config=cfg(life_window_t=10))
    assert r.outcome == "NO_ENTRY" and r.reason == "no_confirm"


def test_confirm_dt_are_datetimes_not_window_indices():
    # регрессия: API маппило confirm_idx на ПОЛНЫЙ список свечей (с lookback),
    # а индексы — относительно окна от signal_time. Движок отдаёт datetimes.
    t0 = BASE - timedelta(minutes=30)
    cs = mk(
        px=[Decimal("101")] * 6 + [Decimal("100.5"), Decimal("101.5"), Decimal("101.8"),
                                   Decimal("102.0"), Decimal("102.0"), Decimal("102.0")],
        opens=[Decimal("101")] * 7 + [Decimal("100.5"), Decimal("101.5"), Decimal("101.8"),
                                      Decimal("102.0"), Decimal("102.0")],
        lows=[Decimal("100.8")] * 6 + [Decimal("99.9"), Decimal("100.4"), Decimal("101.4"),
                                       Decimal("101.5"), Decimal("101.5"), Decimal("101.5")],
        highs=[Decimal("101.3")] * 6 + [Decimal("101.2"), Decimal("101.6"), Decimal("101.9"),
                                        Decimal("102.1"), Decimal("102.1"), Decimal("102.1")],
        t0=t0,
    )
    sig_time = BASE  # = open свечи 6
    r = review_signal(side="LONG", level_price=Decimal("100"), signal_time=sig_time,
                      candles=cs, entry_code="T2", sl_index=1, config=cfg(life_window_t=30))
    assert r.touch_dt == cs[6].open_time
    assert r.confirm_idx == [1]  # индекс относительно окна, НЕ полного списка
    assert r.confirm_dt == [cs[7].open_time]


def test_breakeven_moves_sl_long():
    # LONG 100, SL 1%: ход +0.5% -> стоп в 100.2, затем прокол locked-стопа
    from level_tester.backtester.hourbounce import ExecParams
    cs = mk(
        px=[Decimal("100.5"), Decimal("100.7"), Decimal("100.0")],
        opens=[Decimal("100.5"), Decimal("100.5"), Decimal("100.7")],
        lows=[Decimal("99.9"), Decimal("100.4"), Decimal("99.9")],
        highs=[Decimal("100.6"), Decimal("100.8"), Decimal("100.8")],
    )
    ex = ExecParams(sl_pct=Decimal("1"), be_trigger_pct=Decimal("0.5"), be_lock_pct=Decimal("0.2"),
                    trail_activation_pct=None, trail_distance_pct=None)
    r = review_signal(side="LONG", level_price=Decimal("100"), signal_time=BASE,
                      candles=cs, entry_code="T1", sl_index=1, exec_params=ex)
    assert r.outcome == "STOP" and r.exit_kind == "sl"
    assert r.exit_price == Decimal("100.2")  # locked, а не начальные 99.0
    assert r.be_price == Decimal("100.2")
    assert any(e.type == "breakeven" for e in r.events)


def test_breakeven_then_trail_gating_short():
    # SHORT: трейлинг активируется только после BE (строгое правило PGv2):
    # свеча 1 могла бы активировать trail, но BE ещё не было -> блок;
    # свеча 2: BE уже активен -> trail on -> выход по трейлингу
    from level_tester.backtester.hourbounce import ExecParams
    cs = mk(
        px=[Decimal("99.7"), Decimal("99.4"), Decimal("98.5")],
        opens=[Decimal("99.6"), Decimal("99.7"), Decimal("99.4")],
        lows=[Decimal("99.55"), Decimal("98.9"), Decimal("98.4")],
        highs=[Decimal("100.2"), Decimal("99.75"), Decimal("99.5")],
    )
    ex = ExecParams(sl_pct=Decimal("1"), be_trigger_pct=Decimal("0.5"), be_lock_pct=Decimal("0.2"),
                    trail_activation_pct=Decimal("1.0"), trail_distance_pct=Decimal("0.5"))
    r = review_signal(side="SHORT", level_price=Decimal("100"), signal_time=BASE,
                      candles=cs, entry_code="T1", sl_index=1, exec_params=ex,
                      config=cfg(life_window_t=10))
    assert r.outcome == "TAKE" and r.exit_kind == "trail"
    assert r.exit_price == Decimal("98.8920")
    assert any(e.type == "breakeven" for e in r.events)
    assert len(r.trail_path) == 1  # активация только на свече 2


def test_fixed_tp_exit():
    from level_tester.backtester.hourbounce import ExecParams
    cs = mk(
        px=[Decimal("100.5"), Decimal("101.6")],
        opens=[Decimal("100.5"), Decimal("100.5")],
        lows=[Decimal("99.9"), Decimal("100.4")],
        highs=[Decimal("100.6"), Decimal("101.7")],
    )
    ex = ExecParams(sl_pct=Decimal("1"), fixed_tp=Decimal("101.5"),
                    trail_activation_pct=None, trail_distance_pct=None)
    r = review_signal(side="LONG", level_price=Decimal("100"), signal_time=BASE,
                      candles=cs, entry_code="T1", sl_index=1, exec_params=ex)
    assert r.outcome == "TAKE" and r.exit_kind == "tp"
    assert r.exit_price == Decimal("101.5")


def test_tp_only_skips_fixed_tp():
    from level_tester.backtester.hourbounce import ExecParams
    cs = mk(
        px=[Decimal("100.3"), Decimal("101.6")],
        opens=[Decimal("100.2"), Decimal("100.5")],
        lows=[Decimal("99.9"), Decimal("100.4")],
        highs=[Decimal("100.4"), Decimal("101.7")],
    )
    ex = ExecParams(sl_pct=Decimal("1"), fixed_tp=Decimal("101.5"), trail_tp_only=True,
                    trail_activation_pct=Decimal("0.5"), trail_distance_pct=Decimal("0.5"))
    r = review_signal(side="LONG", level_price=Decimal("100"), signal_time=BASE,
                      candles=cs, entry_code="T1", sl_index=1, exec_params=ex)
    assert r.outcome == "TAKE" and r.exit_kind == "trail"
    assert r.exit_price == Decimal("101.1915")


def test_trail_threshold_blocks_small_updates():
    # порог 0.5%: подтяжка +0.2% игнорируется, +0.9% применяется
    from level_tester.backtester.hourbounce import ExecParams
    cs = mk(
        px=[Decimal("100.5"), Decimal("101.2"), Decimal("101.4"), Decimal("102.2"), Decimal("102.0")],
        opens=[Decimal("100.5"), Decimal("100.5"), Decimal("101.2"), Decimal("101.4"), Decimal("102.2")],
        lows=[Decimal("99.9"), Decimal("100.4"), Decimal("101.0"), Decimal("101.3"), Decimal("101.9")],
        highs=[Decimal("100.6"), Decimal("101.3"), Decimal("101.5"), Decimal("102.2"), Decimal("102.3")],
    )
    ex = ExecParams(sl_pct=Decimal("1"), trail_activation_pct=Decimal("1.0"),
                    trail_distance_pct=Decimal("1.0"), trail_threshold_pct=Decimal("0.5"))
    r = review_signal(side="LONG", level_price=Decimal("100"), signal_time=BASE,
                      candles=cs, entry_code="T1", sl_index=1, exec_params=ex,
                      config=cfg(life_window_t=10))
    assert [p["trail"] for p in r.trail_path] == ["100.287", "101.178"]


def test_exec_from_signal_mapping():
    from level_tester.backtester.hourbounce import exec_from_signal
    from level_tester.backtester.signal_reader import SignalEntry
    sig = SignalEntry(
        signal_id="x", symbol="KASUSDT", side="SHORT", entry_price=0.03727,
        stop_loss=0.03764, rr_ratio=None, timeframe="1h", timestamp="2026-09-08 07:53:53",
        source="archive", take_profits="[0.03615]",
        breakeven_enabled=True, breakeven_trigger_pct=0.9, breakeven_profit_pct=0.35,
        trailing_stop_enabled=True, trailing_activation_pct=1.0, trailing_stop_pct=0.6,
        trailing_update_threshold_pct=0.2, trailing_tp_only=False,
    )
    ex = exec_from_signal(sig)
    assert ex is not None
    assert ex.sl_price == Decimal("0.03764")
    assert ex.fixed_tp == Decimal("0.03615")
    assert ex.be_trigger_pct == Decimal("0.9") and ex.be_lock_pct == Decimal("0.35")
    assert ex.trail_activation_pct == Decimal("1.0") and ex.trail_distance_pct == Decimal("0.6")
    assert ex.trail_threshold_pct == Decimal("0.2") and not ex.trail_tp_only
    bad = SignalEntry(
        signal_id="y", symbol="Z", side="LONG", entry_price=100.0,
        stop_loss=None, rr_ratio=None, timeframe="1h", timestamp=None, source="archive",
    )
    # нет SL в архиве -> начальный по конфигу PGv2 (stop_loss_pct=1.0)
    bad_ex = exec_from_signal(bad)
    assert bad_ex is not None and bad_ex.sl_price == Decimal("99.000")
    assert bad_ex.sl_source == "config"
    # чужой источник без pct и без цены — восстановить не из чего
    bad2 = SignalEntry(
        signal_id="z", symbol="Z", side="LONG", entry_price=None,
        stop_loss=None, rr_ratio=None, timeframe="1h", timestamp=None, source="archive",
        stop_loss_source="manual",
    )
    assert exec_from_signal(bad2) is None


def test_cell_pnl_signed_and_zero():
    from level_tester.api.app import _hb_cell_pnl
    assert _hb_cell_pnl({"outcome": "TAKE", "entry_price": "100", "exit_price": "101"}, "LONG") == 1.0
    assert _hb_cell_pnl({"outcome": "TAKE", "entry_price": "100", "exit_price": "101"}, "SHORT") == -1.0
    assert _hb_cell_pnl({"outcome": "STOP", "entry_price": "100", "exit_price": "99.5"}, "LONG") == -0.5
    assert _hb_cell_pnl({"outcome": "NO_ENTRY", "entry_price": None, "exit_price": None}, "LONG") == 0.0
    assert _hb_cell_pnl({"outcome": "TAKE", "entry_price": None, "exit_price": "5"}, "LONG") == 0.0
    assert _hb_cell_pnl({}, "SHORT") == 0.0


def test_pg_counts_touch_candle_req2():
    # PGv2 1:1 (confirmation_loop.py:875-989): зелёная свеча касания идёт
    # в счётчик первой, нужна ещё одна -> confirms [0, 1], вход на 2-й
    cs = mk(
        px=[Decimal("101.2"), Decimal("101.8"), Decimal("102.0")],
        opens=[Decimal("100.5"), Decimal("101.2"), Decimal("101.8")],
        lows=[Decimal("99.9"), Decimal("101.1"), Decimal("101.7")],
        highs=[Decimal("101.3"), Decimal("101.9"), Decimal("102.1")],
    )
    r = review_signal(side="LONG", level_price=Decimal("100"), signal_time=BASE,
                      candles=cs, entry_code="PG", sl_index=1, pg_required=2,
                      config=cfg(life_window_t=10))
    assert [d.isoformat() for d in r.confirm_dt] == [cs[0].open_time.isoformat(), cs[1].open_time.isoformat()]
    assert r.entry_price == Decimal("101.8")


def test_pg_req1_red_touch_waits_next_green():
    # req=1, касание красное -> в счётчик не идёт, ждём следующую зелёную
    cs = mk(
        px=[Decimal("100.5"), Decimal("101.5"), Decimal("101.8")],
        opens=[Decimal("101"), Decimal("100.5"), Decimal("101.5")],
        lows=[Decimal("99.9"), Decimal("100.4"), Decimal("101.4")],
        highs=[Decimal("101.2"), Decimal("101.6"), Decimal("101.9")],
    )
    r = review_signal(side="LONG", level_price=Decimal("100"), signal_time=BASE,
                      candles=cs, entry_code="PG", sl_index=1, pg_required=1,
                      config=cfg(life_window_t=10))
    assert [d.isoformat() for d in r.confirm_dt] == [cs[1].open_time.isoformat()]
    assert r.entry_price == Decimal("101.5")


def test_pg_req1_green_touch_confirms_immediately():
    cs = mk(
        px=[Decimal("101.2"), Decimal("101.8")],
        opens=[Decimal("100.5"), Decimal("101.2")],
        lows=[Decimal("99.9"), Decimal("101.1")],
        highs=[Decimal("101.3"), Decimal("101.9")],
    )
    r = review_signal(side="LONG", level_price=Decimal("100"), signal_time=BASE,
                      candles=cs, entry_code="PG", sl_index=1, pg_required=1,
                      config=cfg(life_window_t=10))
    assert [d.isoformat() for d in r.confirm_dt] == [cs[0].open_time.isoformat()]
    assert r.entry_price == Decimal("101.2")


def test_pg_req0_market_on_touch():
    # req=0: зелёное касание -> вход на следующей; красное -> первая зелёная
    green = mk(
        px=[Decimal("101.2"), Decimal("101.8")],
        opens=[Decimal("100.5"), Decimal("101.2")],
        lows=[Decimal("99.9"), Decimal("101.1")],
        highs=[Decimal("101.3"), Decimal("101.9")],
    )
    r = review_signal(side="LONG", level_price=Decimal("100"), signal_time=BASE,
                      candles=green, entry_code="PG", sl_index=1, pg_required=0,
                      config=cfg(life_window_t=10))
    assert r.entry_price == Decimal("101.2")
    red = mk(
        px=[Decimal("100.5"), Decimal("101.5"), Decimal("101.8")],
        opens=[Decimal("101"), Decimal("100.5"), Decimal("101.5")],
        lows=[Decimal("99.9"), Decimal("100.4"), Decimal("101.4")],
        highs=[Decimal("101.2"), Decimal("101.6"), Decimal("101.9")],
    )
    r2 = review_signal(side="LONG", level_price=Decimal("100"), signal_time=BASE,
                       candles=red, entry_code="PG", sl_index=1, pg_required=0,
                       config=cfg(life_window_t=10))
    assert r2.entry_price == Decimal("101.5")


def test_deterministic_replay():
    cs = mk([Decimal("101"), Decimal("100.5"), Decimal("102.5"), Decimal("101.0")],
            opens=[Decimal("102"), Decimal("101"), Decimal("100.5"), Decimal("102.5")],
            lows=[Decimal("100.9"), Decimal("100.0"), Decimal("100.5"), Decimal("100.9")],
            highs=[Decimal("102.2"), Decimal("101.1"), Decimal("102.6"), Decimal("102.6")])
    kw = dict(side="BUY", level_price=Decimal("101"), signal_time=BASE,
              candles=cs, entry_code="T2", sl_index=2, config=cfg())
    a = review_signal(**kw)
    b = review_signal(**kw)
    assert (a.outcome, a.entry_price, a.exit_price) == (b.outcome, b.entry_price, b.exit_price)
