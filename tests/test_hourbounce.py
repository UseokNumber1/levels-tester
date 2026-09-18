"""Unit-кейсы движка HourBounce из ТЗ v2.0 п.5 (обязательные)."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from level_tester.backtester.hourbounce import HourBounceConfig, config_for_tf, review_signal
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


def test_confirm_window_is_time_based_across_tf():
    # касание на i=0, единственное подтверждение на баре 50 (~50 мин спустя):
    # в окно M1 (100 бар = 100 мин) попадает, в окно M5 (20 бар = 100 мин,
    # но 50 бар M5 = 250 мин) — нет при том же числе баров; здесь бары
    # одинаковые, поэтому M5-окно 20 его не видит, а M1-окно 100 видит.
    px = [Decimal("99.5")] * 50 + [Decimal("100.5")] + [Decimal("100.5")] * 9
    opens = [Decimal("101")] + [Decimal("99.5")] * 49 + [Decimal("99.8")] + [Decimal("100.5")] * 9
    cs = mk(px=px, opens=opens)
    r1 = review_signal(side="BUY", level_price=Decimal("100"), signal_time=BASE,
                       candles=cs, entry_code="T2", sl_index=1, config=config_for_tf("1m"))
    assert r1.entry_dt is not None and r1.reason != "no_confirm"
    r5 = review_signal(side="BUY", level_price=Decimal("100"), signal_time=BASE,
                       candles=cs, entry_code="T2", sl_index=1, config=config_for_tf("5m"))
    assert r5.outcome == "NO_ENTRY" and r5.reason == "no_confirm"


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


def test_grid_be_exec_params_from_config():
    from level_tester.backtester.hourbounce import (
        GRID_BE_LOCK_PCT,
        GRID_BE_TRIGGER_PCT,
        grid_be_exec,
    )
    assert (GRID_BE_TRIGGER_PCT, GRID_BE_LOCK_PCT) == (Decimal("0.9"), Decimal("0.35"))
    ex = grid_be_exec(2)
    assert ex.sl_pct == Decimal("1.0")
    assert ex.be_trigger_pct == Decimal("0.9") and ex.be_lock_pct == Decimal("0.35")
    assert ex.use_be and ex.use_trail
    assert ex.trail_activation_pct == Decimal("1.0") and ex.trail_distance_pct == Decimal("1.0")


def test_grid_be_moves_stop_same_candle_trail_next():
    # Grid+БУ LONG: свеча 1 бьёт БУ-триггер 0.9% (стоп -> 100.35 в той же свече),
    # но трейлинг в той же свече заблокирован гейтом; свеча 2 бьёт активацию
    # 1.0% -> trail_on. Порядок как в проде PGv2.
    from level_tester.backtester.hourbounce import grid_be_exec
    cs = mk(
        px=[Decimal("100.2"), Decimal("100.6"), Decimal("101.0"), Decimal("101.0")],
        opens=[Decimal("100.5"), Decimal("100.4"), Decimal("100.5"), Decimal("101.0")],
        lows=[Decimal("99.9"), Decimal("100.38"), Decimal("100.5"), Decimal("100.9")],
        highs=[Decimal("100.6"), Decimal("100.95"), Decimal("101.2"), Decimal("101.1")],
    )
    ex = grid_be_exec(1)
    r = review_signal(side="LONG", level_price=Decimal("100"), signal_time=BASE,
                      candles=cs, entry_code="T1", sl_index=1, exec_params=ex)
    be = [e for e in r.events if e.type == "breakeven"]
    tr = [e for e in r.events if e.type == "trail_on"]
    assert len(be) == 1 and be[0].dt == cs[1].close_time
    assert r.be_price == Decimal("100.35")
    assert len(tr) == 1 and tr[0].dt == cs[2].close_time
    assert r.outcome == "EXPIRED"  # стоп 100.35 и trail 100.188 не задеты


def test_review_matrix_grid_be_has_nine_cells_with_be():
    from level_tester.backtester.hourbounce import review_matrix
    cs = mk(
        px=[Decimal("100.2"), Decimal("100.5"), Decimal("101.0"), Decimal("101.0")],
        opens=[Decimal("101"), Decimal("100.2"), Decimal("100.5"), Decimal("101.0")],
        lows=[Decimal("99.9"), Decimal("100.2"), Decimal("100.5"), Decimal("100.9")],
        highs=[Decimal("101.1"), Decimal("100.95"), Decimal("101.2"), Decimal("101.1")],
    )
    m1s = mk1([
        (101.0, 101.1, 99.9, 100.2),
        (100.2, 100.6, 100.1, 100.5),
        (100.5, 101.0, 100.4, 100.9),
        (100.9, 101.2, 100.8, 101.1),
        (101.1, 101.3, 101.0, 101.2),
    ])
    kw = dict(side="LONG", level_price=Decimal("100"), signal_time=BASE, candles=cs)
    plain = review_matrix(**kw, exec_mode="grid", m1_candles=m1s)
    be = review_matrix(**kw, exec_mode="grid_be", m1_candles=m1s)
    assert len(plain) == len(be) == 9
    assert {r.entry_code for r in plain} == {"T1M", "T2", "T3"}
    assert not any(e.type == "breakeven" for r in plain for e in r.events)
    t1m = [r for r in plain if r.entry_code == "T1M"]
    assert all(r.outcome != "NO_ENTRY" for r in t1m)
    t1m_be = next(r for r in be if r.entry_code == "T1M" and r.sl_index == 1)
    assert any(e.type == "breakeven" for e in t1m_be.events)


def test_review_matrix_t1m_without_m1_is_no_entry():
    from level_tester.backtester.hourbounce import review_matrix
    cs = mk(
        px=[Decimal("100.2"), Decimal("100.5"), Decimal("100.6")],
        opens=[Decimal("101"), Decimal("100.2"), Decimal("100.5")],
        lows=[Decimal("99.9"), Decimal("100.2"), Decimal("100.4")],
        highs=[Decimal("101.1"), Decimal("100.95"), Decimal("100.8")],
    )
    res = review_matrix(side="LONG", level_price=Decimal("100"), signal_time=BASE,
                        candles=cs, exec_mode="grid")
    t1m = [r for r in res if r.entry_code == "T1M"]
    assert len(t1m) == 3
    assert all(r.outcome == "NO_ENTRY" and r.reason == "no_m1" for r in t1m)
    # T2/T3 на M5 при этом считаются как раньше
    assert any(r.outcome != "NO_ENTRY" for r in res if r.entry_code == "T2")


def mk1(rows, t0=BASE):
    """Минутные свечи: rows = [(open, high, low, close), ...]."""
    return [
        Candle(
            t0 + i * timedelta(minutes=1), t0 + (i + 1) * timedelta(minutes=1),
            Decimal(str(o)), Decimal(str(h)), Decimal(str(lo)), Decimal(str(c)),
            Decimal("1"), "1m", True,
        )
        for i, (o, h, lo, c) in enumerate(rows)
    ]


def agg5(m1s):
    """Собрать M5 из кратных 5 M1."""
    out = []
    for g in range(0, len(m1s), 5):
        part = m1s[g:g + 5]
        out.append(Candle(
            part[0].open_time, part[-1].close_time,
            part[0].open, max(c.high for c in part),
            min(c.low for c in part), part[-1].close,
            Decimal("5"), "5m", True,
        ))
    return out


def test_t1m_basic_market_on_touch():
    # касание 100 на минуте 2 (low 99.9), вход по принту = level, исполнение на M1
    m1s = mk1([
        (100.5, 100.6, 100.4, 100.5),
        (100.5, 100.6, 100.3, 100.4),
        (100.4, 100.5, 99.9, 100.1),
        (100.1, 101.2, 100.0, 101.0),
        (101.0, 101.5, 100.9, 101.4),
        (101.4, 101.8, 101.3, 101.7),
    ])
    r = review_signal(side="LONG", level_price=Decimal("100"), signal_time=BASE,
                      candles=agg5(m1s + mk1([
                          (101.7, 101.9, 101.6, 101.8),
                          (101.8, 102.0, 101.7, 101.9),
                          (101.9, 102.1, 101.8, 102.0),
                          (102.0, 102.2, 101.9, 102.1),
                      ], t0=BASE + timedelta(minutes=6))),
                      entry_code="T1M", sl_index=1, m1_candles=m1s)
    assert r.entry_code == "T1M"
    assert r.entry_price == Decimal("100")
    assert r.entry_dt == BASE + timedelta(minutes=2)
    assert r.touch_dt == BASE + timedelta(minutes=2)
    assert r.outcome in ("TAKE", "STOP", "EXPIRED")


def test_t1m_gap_entry_at_open():
    # минута открылась сразу под уровнем (гэп): маркет исполняется по open
    m1s = mk1([
        (99.5, 99.6, 99.4, 99.5),
        (99.5, 99.7, 99.3, 99.6),
    ])
    r = review_signal(side="LONG", level_price=Decimal("100"), signal_time=BASE,
                      candles=agg5(m1s + mk1([
                          (99.6, 99.8, 99.5, 99.7),
                          (99.7, 99.9, 99.6, 99.8),
                          (99.8, 100.0, 99.7, 99.9),
                      ], t0=BASE + timedelta(minutes=2))),
                      entry_code="T1M", sl_index=1, m1_candles=m1s)
    assert r.entry_price == Decimal("99.5")
    assert r.entry_dt == BASE


def test_t1x_no_m1():
    m5 = mk(px=[Decimal("100.5")], opens=[Decimal("101")], lows=[Decimal("99.9")])
    for code in ("T1M", "T1L"):
        r = review_signal(side="LONG", level_price=Decimal("100"), signal_time=BASE,
                          candles=m5, entry_code=code, sl_index=1)
        assert r.outcome == "NO_ENTRY" and r.reason == "no_m1"


def test_t1m_slippage_against_trader():
    m1s = mk1([
        (100.5, 100.6, 100.4, 100.5),
        (100.4, 100.5, 99.9, 100.1),
        (100.1, 100.4, 100.0, 100.3),
        (100.3, 100.5, 100.2, 100.4),
        (100.4, 100.6, 100.3, 100.5),
    ])
    kw = dict(side="LONG", level_price=Decimal("100"), signal_time=BASE,
              candles=agg5(m1s), entry_code="T1M", sl_index=1, m1_candles=m1s)
    base = review_signal(**kw)
    slip = review_signal(**kw, slippage_pct=Decimal("0.1"))
    assert base.entry_price == Decimal("100")
    assert slip.entry_price == Decimal("100") * Decimal("1.001")
    assert slip.entry_price > base.entry_price


def test_t1l_clips_pretouch_spike():
    # спайк 101.5 до касания: T1 берёт фантомный trail, T1L — только post-touch
    m1s = mk1([
        (100.5, 101.5, 100.4, 101.3),
        (101.3, 101.4, 100.8, 100.9),
        (100.9, 101.0, 99.9, 100.0),
        (100.0, 100.3, 99.95, 100.2),
        (100.2, 100.4, 100.0, 100.3),
        (100.3, 101.0, 100.2, 100.9),
        (100.9, 101.6, 100.8, 101.5),
        (101.5, 102.2, 101.4, 102.0),
        (102.0, 102.5, 101.9, 102.3),
        (102.3, 102.6, 102.2, 102.5),
    ])
    m5 = agg5(m1s)
    kw = dict(side="LONG", level_price=Decimal("100"), signal_time=BASE, candles=m5, sl_index=1)
    t1 = review_signal(**kw, entry_code="T1")
    t1l = review_signal(**kw, entry_code="T1L", m1_candles=m1s)
    assert t1l.entry_code == "T1L"
    assert t1l.entry_price == Decimal("100")
    # T1 активировался от дотрогательного спайка, T1L — нет
    assert t1.exit_price != t1l.exit_price


def test_t1l_fills_on_touch_print():
    # условие: лимитка уже стоит и заливается сразу в касание — вход всегда
    # по level, даже если уровень пройден между минутами без возврата
    m1s = mk1([
        (100.5, 100.6, 100.4, 100.5),
        (100.5, 100.5, 99.0, 99.1),  # пролив через уровень — заливка в касание
        (99.1, 99.2, 98.9, 99.0),
        (99.0, 99.1, 98.8, 98.9),
        (98.9, 99.0, 98.7, 98.8),
    ])
    m5 = agg5(m1s)
    kw = dict(side="LONG", level_price=Decimal("100"), signal_time=BASE, candles=m5, sl_index=1)
    t1l = review_signal(**kw, entry_code="T1L", m1_candles=m1s)
    assert t1l.outcome != "NO_ENTRY" and t1l.entry_price == Decimal("100")

    # гэп между минутами через уровень без возврата: касание было (принт 100
    # между закрытием минуты 0 и открытием минуты 1) — заливка по принту = level
    m1s2 = mk1([
        (100.5, 100.6, 100.4, 100.5),
        (99.5, 99.6, 99.4, 99.5),  # открылись под уровнем, весь диапазон ниже
        (99.5, 99.7, 99.3, 99.6),
        (99.6, 99.7, 99.4, 99.5),
        (99.5, 99.6, 99.2, 99.3),
    ])
    m5b = agg5(m1s2)
    kw2 = dict(side="LONG", level_price=Decimal("100"), signal_time=BASE, candles=m5b, sl_index=1)
    t1l2 = review_signal(**kw2, entry_code="T1L", m1_candles=m1s2)
    assert t1l2.outcome != "NO_ENTRY" and t1l2.entry_price == Decimal("100")


def test_t1l_no_m1_touch_on_data_hole():
    # M5 касается, а в M1-ряде касания нет (дыра в данных) — строгий NO_ENTRY
    m1s = mk1([
        (100.5, 100.6, 100.4, 100.5),
        (100.5, 100.6, 100.4, 100.5),
        (100.6, 100.7, 100.5, 100.6),
        (100.6, 100.7, 100.5, 100.6),
        (100.7, 100.8, 100.6, 100.7),
    ])
    m5 = agg5(m1s)
    # вручную опускаем low M5-касания ниже уровня: M1 касания нет, M5 — есть
    from dataclasses import replace as _replace
    touched = _replace(m5[0], low=Decimal("99.9"))
    m5h = [touched, *m5[1:]] if len(m5) > 1 else [touched]
    r = review_signal(side="LONG", level_price=Decimal("100"), signal_time=BASE,
                      candles=m5h, entry_code="T1L", sl_index=1, m1_candles=m1s)
    assert r.outcome == "NO_ENTRY" and r.reason == "no_m1_touch"


def test_t1l_limit_fill_on_return():
    # минута целиком под уровнем (без заливки), позже возврат к уровню -> вход по level
    m1s = mk1([
        (100.5, 100.6, 100.4, 100.5),
        (100.5, 100.6, 100.2, 100.3),
        (99.8, 99.9, 99.5, 99.6),     # целиком под уровнем — не заливка
        (99.6, 100.2, 99.5, 100.1),   # возврат к уровню — заливка здесь
        (100.1, 100.5, 100.0, 100.4),
    ])
    m5 = agg5(m1s)
    r = review_signal(side="LONG", level_price=Decimal("100"), signal_time=BASE,
                      candles=m5, entry_code="T1L", sl_index=1, m1_candles=m1s)
    assert r.outcome != "NO_ENTRY" and r.entry_price == Decimal("100")
