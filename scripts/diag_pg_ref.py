#!/usr/bin/env python3
"""Диагностика PG-эталона: сверка реплея review_signal(PG) с архивом.

Для каждой сентябрьской traded-сделки показывает:
arch_entry/exit/pnl vs replay_entry/exit/pnl + какой SL взят и откуда.
"""
import sys
from datetime import UTC, datetime
from decimal import Decimal

sys.path.insert(0, "E:\\Pyton_project\\levels-tester\\src")

import level_tester.api.app as appmod
from level_tester.backtester.hourbounce import (
    config_for_tf,
    exec_from_signal,
    review_signal,
)
from level_tester.backtester.signal_reader import SignalReader
from level_tester.infrastructure.hourbounce_store import load_candles_cached


def main() -> None:
    reader = SignalReader(appmod.PGV2_TRADING_DB, appmod.PGV2_ARCHIVE_DB)
    sigs = appmod._hb_signals_september_traded() if hasattr(appmod, "_hb_signals_september_traded") else None
    if sigs is None:
        import json as _json
        import urllib.request as _url

        with _url.urlopen(
            "http://127.0.0.1:8080/api/hourbounce/signals"
            "?traded_only=true&date_from=2026-09-01&date_to=2026-09-30&limit=5000"
        ) as r:
            sigs = _json.load(r)["items"]
    cfg = config_for_tf("1m")
    print(f"{'signal':44} {'side':5} {'arch_entry':>12} {'replay_entry':>12} "
          f"{'ediff%':>7} {'sl_src':>6} {'arch_exit':>12} {'replay_exit':>12} "
          f"{'arch%':>7} {'replay%':>7}")
    for s in sigs:
        sid = s["signal_id"]
        try:
            found = reader.read(signal_id=sid, include_trading=False,
                                include_archive=True, limit=1)
            if not found:
                print(f"{sid:44} NO SIGNAL IN READER")
                continue
            sig = found[0]
            ex = exec_from_signal(sig)
            meta = appmod._hb_archive_meta(sid)
            _dtp, place_dt = appmod._hb_dt_place(
                sid, meta.get("placement_date"), sig.timestamp)
            if place_dt is None or ex is None:
                print(f"{sid:44} NO TIME/PARAMS")
                continue
            try:
                req = int(meta.get("required_bars", 2))
            except (TypeError, ValueError):
                req = 2
            start = place_dt - __import__("datetime").timedelta(minutes=30)
            now_floor = datetime.now(UTC).replace(second=0, microsecond=0)
            end = min(place_dt + __import__("datetime").timedelta(minutes=10000),
                      now_floor)
            candles, _stats = load_candles_cached(
                appmod.session_factory, appmod.binance_client, sig.symbol,
                start, end, refresh=False, tf="1m")
            if not candles:
                print(f"{sid:44} NO CANDLES")
                continue
            level = Decimal(str(s.get("level_price") or sig.entry_price))
            res = review_signal(
                side=s["side"], level_price=level, signal_time=place_dt,
                candles=candles, entry_code="PG", sl_index=0, config=cfg,
                exec_params=ex, pg_required=req, tf="1m")
            arch = appmod._hb_archive_trades([sid]).get(sid, {})
            import sqlite3 as _sq

            ax = 0.0
            adb = str(appmod.PGV2_ARCHIVE_DB)
            _conn = _sq.connect(f"file:{adb}?mode=ro", uri=True)
            try:
                _conn.row_factory = _sq.Row
                _row = _conn.execute(
                    "SELECT entry_price, exit_price, status, pnl_percent FROM signal_archive"
                    " WHERE original_id = ? OR id = ? LIMIT 1", (sid, sid)).fetchone()
                if _row:
                    ax = float(_row["exit_price"] or 0.0)
            finally:
                _conn.close()
            re_ = float(res.entry_price) if res.entry_price else 0.0
            ae = float(sig.entry_price or 0.0)
            ediff = ((re_ - ae) / ae * 100) if ae else 0.0
            rx = float(res.exit_price) if res.exit_price else 0.0
            print(f"{sid:44} {s['side']:5} {ae:12.6f} {re_:12.6f} "
                  f"{ediff:7.2f} {ex.sl_source:>6} {ax:12.6f} {rx:12.6f} "
                  f"{str(arch.get('pnl_percent')):>7} {float(res.net_pnl_pct or 0.0):7.2f} "
                  f"{res.outcome}/{res.exit_kind}")
        except Exception as exc:  # noqa: BLE001 - диагностика не падает
            print(f"{sid:44} ERROR {exc}")


if __name__ == "__main__":
    main()
