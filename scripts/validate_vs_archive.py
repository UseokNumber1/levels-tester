"""Сверка тестера с фактом: закрытые сделки архива PGv2 прогоняются нашим
движком 1:1 (параметры SL/TP/BE/trailing из сигнала) и сравниваются с
реальными исходом и PnL%.

Использование (из корня проекта, venv активен):
    .venv\\Scripts\\python.exe scripts\\validate_vs_archive.py --limit 15

Что сравнивается:
- исход: closed_tp*/trailing_tp -> TAKE, closed_sl* -> STOP, closed_be -> BE-выход;
- PnL%: архивный pnl_percent против знакового (exit-entry)/entry*100 реплея.

Заведомые расхождения (не баги, фиксируем как допуски):
- вход: наш T1M/T1L/T2/T3 (маркет-M1/лимит-M1/open) против реального маркет/лимит-филла PGv2;
- комиссии и парциал breakeven_fix 50% не моделируем (наш PnL — гросс);
- closed_be_filled: PGv2 мог фиксить часть позиции (be_fix), у нас — полный выход.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from level_tester.backtester.hourbounce import (  # noqa: E402
    DEFAULT_CONFIG,
    exec_from_signal,
    review_signal,
)
from level_tester.backtester.signal_reader import SignalReader  # noqa: E402
from level_tester.infrastructure.binance import BinanceFuturesClient  # noqa: E402

TRADING_DB = Path(r"E:\Pyton_project\precision_grid_v2\data\db\trading.db")
ARCHIVE_DB = Path(r"E:\Pyton_project\precision_grid_v2\data\db\signal_archive.db")

CLOSED_STATUSES = (
    "closed_sl", "closed_sl_filled", "closed_tp", "closed_tp_filled",
    "closed_trailing_tp_filled", "closed_be_filled",
)


def arch_outcome(status: str, reason: str | None) -> str:
    s, r = (status or "").lower(), (reason or "").lower()
    if "trailing" in s or "trailing" in r:
        return "TRAIL"
    if "tp" in s or "tp" in r or "take" in r:
        return "TAKE"
    if "sl" in s or "stop" in r or "sl_" in r:
        return "STOP"
    if "be" in s or "be_" in r or "breakeven" in r:
        return "BE"
    return "?"


def parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def load_closed(limit: int) -> list[dict]:
    conn = sqlite3.connect(f"file:{ARCHIVE_DB.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        q = f"SELECT *, json_extract(metadata,'$.confirmation_bars_required') AS req_bars FROM signal_archive WHERE status IN ({','.join('?' * len(CLOSED_STATUSES))}) ORDER BY closed_at DESC LIMIT ?"
        return [dict(r) for r in conn.execute(q, (*CLOSED_STATUSES, limit)).fetchall()]
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--lookforward", type=int, default=5000)
    args = ap.parse_args()

    client = BinanceFuturesClient()
    reader = SignalReader(TRADING_DB, ARCHIVE_DB)
    rows = load_closed(args.limit)

    print(f"{'symbol':12} {'tf':3} {'side':5} {'arch':6} {'archPnL':>8} | {'PG':4} {'наш T1M':5} {'наш T1L':5} {'наш T2':4} {'наш T3':4} | {'PnL наш/арх (PG)':>22} | итог")
    print("-" * 130)
    match = mismatch = skipped = 0
    for a in rows:
        sid = a["original_id"] or a["id"]
        found = reader.read(signal_id=sid, include_trading=False, include_archive=True, limit=1)
        if not found:
            print(f"{a['symbol']:12} нет в reader — пропуск")
            skipped += 1
            continue
        sig = found[0]
        ex = exec_from_signal(sig)
        if ex is None:
            print(f"{a['symbol']:12} нет SL в архиве — пропуск")
            skipped += 1
            continue
        from level_tester.api.app import _hb_dt_place  # noqa: E402

        try:
            import json as _json

            _meta = _json.loads(a.get("metadata") or "{}")
        except Exception:
            _meta = {}
        _, sig_time = _hb_dt_place(sid, None, sig.timestamp, _meta.get("confirmation_waiting_started_at"))
        if sig_time is None:
            skipped += 1
            continue
        tf = (sig.confirmation_timeframe or "5m").lower()
        if tf not in ("1m", "3m", "5m", "15m"):
            tf = "5m"
        start = sig_time
        end = min(
            sig_time + args.lookforward * __import__("datetime").timedelta(minutes=5),
            datetime.now(UTC),
        )
        try:
            candles = client.klines(sig.symbol, tf, start, end)
            m1 = client.klines(sig.symbol, "1m", start, end)
        except Exception as exc:
            print(f"{a['symbol']:12} свечи не загрузились: {exc}")
            skipped += 1
            continue
        if not candles:
            print(f"{a['symbol']:12} пустые свечи — пропуск")
            skipped += 1
            continue
        outs = {}
        # PG-режим 1:1: required баров подряд СЧИТАЯ касание + параметры сделки.
        # Рядом для контекста T-сетка (T1M — маркет на касании по M1,
        # T1L — лимитка с заливкой по level, T2/T3 — M5).
        try:
            req = int(a.get("req_bars") or 2)
        except (TypeError, ValueError):
            req = 2
        pg = review_signal(side=sig.side, level_price=Decimal(str(sig.entry_price)),
                           signal_time=sig_time, candles=candles, entry_code="PG",
                           sl_index=0, config=DEFAULT_CONFIG, exec_params=ex,
                           pg_required=req)
        for code in ("T1M", "T1L", "T2", "T3"):
            r = review_signal(side=sig.side, level_price=Decimal(str(sig.entry_price)),
                              signal_time=sig_time, candles=candles, entry_code=code,
                              sl_index=0, config=DEFAULT_CONFIG, exec_params=ex,
                              m1_candles=m1)
            outs[code] = r.outcome
        if pg.entry_price and pg.exit_price and pg.outcome in ("TAKE", "STOP"):
            e, x = float(pg.entry_price), float(pg.exit_price)
            o_pnl = round((x - e) / e * 100 if sig.side == "LONG" else (e - x) / e * 100, 4)
        else:
            o_pnl = None
        a_out = arch_outcome(a["status"], a.get("close_reason"))
        a_pnl = a["pnl_percent"]
        ours = pg.outcome
        arch_win = a_out in ("TAKE", "TRAIL")
        ok = (arch_win and ours == "TAKE") or (a_out == "STOP" and ours == "STOP")
        flag = "OK " if ok else ("~BE" if a_out == "BE" else "РАСХОЖДЕНИЕ")
        if ok:
            match += 1
        elif a_out != "BE":
            mismatch += 1
        sl_info = f"SL {ex.sl_price} ({ex.sl_source})"
        pg_in = pg.entry_price if pg.entry_price else None
        pg_out = pg.exit_price if pg.exit_price else None
        print(f"{a['symbol']:12} {tf:3} {sig.side:5} {a_out:6} {a_pnl if a_pnl is not None else '?':>8} | "
              f"PG:{ours:4} T1M:{outs['T1M']:4} T1L:{outs['T1L']:4} T2:{outs['T2']:4} T3:{outs['T3']:4} | "
              f"{o_pnl if o_pnl is not None else '—':>8} / {a_pnl if a_pnl is not None else '?':>8} | {flag} "
              f"[req {req} {sl_info} вход PG/арх {pg_in}/{a['entry_price']} вых PG/арх {pg_out}/{a['exit_price']}]")
    print("-" * 130)
    print(f"Совпало: {match}, расхождение: {mismatch}, пропущено: {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
