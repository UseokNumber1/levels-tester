#!/usr/bin/env python3
"""CLI entry point for the backtester.

Usage:
    # Test a specific symbol for a period
    python backtest.py --symbol BTCUSDT --period 2026-08-01:2026-08-31

    # Test all symbols for a period
    python backtest.py --period 2026-08-01:2026-08-31

    # Test a specific signal by ID
    python backtest.py --signal-id binance_BTCUSDT_support_104500_20260830

    # With specific entry type and variants
    python backtest.py --symbol SOLUSDT --entry confirmation --variants sl03_rr2,sl03_rr2_t1

    # Custom output path
    python backtest.py --symbol BTCUSDT --period 2026-08-01:2026-08-31 --output my_report.html
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Ensure the src directory is on the path
sys.path.insert(0, str(Path(__file__).parent / "src"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest precision_grid_v2 signals against execution variants",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Signal selection
    sig_group = parser.add_argument_group("Signal selection")
    sig_group.add_argument("--symbol", type=str, help="Symbol to test (e.g. BTCUSDT)")
    sig_group.add_argument("--period", type=str, help="Period (YYYY-MM-DD:YYYY-MM-DD)")
    sig_group.add_argument("--signal-id", type=str, help="Test a specific signal by ID")
    sig_group.add_argument("--side", type=str, choices=["LONG", "SHORT"], help="Filter by side")
    sig_group.add_argument("--no-archive", action="store_true", help="Skip signal_archive.db")

    # Database paths
    db_group = parser.add_argument_group("Database paths")
    db_group.add_argument("--trading-db", type=str,
                          default=r"E:\Pyton_project\precision_grid_v2\data\db\trading.db",
                          help="Path to trading.db")
    db_group.add_argument("--archive-db", type=str,
                          default=r"E:\Pyton_project\precision_grid_v2\data\db\signal_archive.db",
                          help="Path to signal_archive.db")

    # Execution
    exec_group = parser.add_argument_group("Execution")
    exec_group.add_argument("--entry", type=str, default="market",
                            choices=["market", "limit", "confirmation"],
                            help="Entry type (default: market)")
    exec_group.add_argument("--variants", type=str,
                            help="Comma-separated variant IDs (default: all built-in)")
    exec_group.add_argument("--variants-file", type=str,
                            help="Path to custom variants YAML file")

    # Candle loading
    candle_group = parser.add_argument_group("Candle loading")
    candle_group.add_argument("--lookback", type=int, default=50,
                              help="Bars to load before signal (default: 50)")
    candle_group.add_argument("--lookforward", type=int, default=200,
                              help="Bars to load after signal (default: 200)")

    # Confirmation parameters
    conf_group = parser.add_argument_group("Confirmation entry")
    conf_group.add_argument("--confirmation-bars", type=int, default=2,
                            help="Required consecutive bars for confirmation (default: 2)")
    conf_group.add_argument("--confirmation-max-wait", type=int, default=15,
                            help="Max bars to wait for confirmation (default: 15)")

    # Limit entry
    limit_group = parser.add_argument_group("Limit entry")
    limit_group.add_argument("--limit-offset", type=float, default=0.2,
                             help="Limit order offset %% (default: 0.2)")

    # Output
    out_group = parser.add_argument_group("Output")
    out_group.add_argument("--output", type=str, default="backtest_report.html",
                           help="Output HTML file (default: backtest_report.html)")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    print("=" * 60)
    print("  BACKTESTER — precision_grid_v2 signals")
    print("=" * 60)

    # 1. Read signals
    print("\n[1/4] Reading signals from database...")
    from level_tester.backtester.signal_reader import SignalReader
    reader = SignalReader(args.trading_db, args.archive_db)

    if args.signal_id:
        signals = reader.read(signal_id=args.signal_id, include_archive=not args.no_archive)
    else:
        period_start, period_end = (args.period.split(":") if args.period else (None, None))
        signals = reader.read(
            symbol=args.symbol,
            side=args.side,
            period_start=period_start,
            period_end=period_end,
            include_archive=not args.no_archive,
        )

    if not signals:
        print("  No signals found matching filters.")
        sys.exit(1)

    print(f"  Found {len(signals)} signal(s)")
    for s in signals[:5]:
        print(f"    {s.symbol:12s} {s.side:5s} @ {s.entry_price:.6f} [{s.source}]")
    if len(signals) > 5:
        print(f"    ... and {len(signals) - 5} more")

    # 2. Load variants
    print("\n[2/4] Loading execution variants...")
    from level_tester.backtester.variants import get_builtin, load_variants_from_yaml

    if args.variants_file:
        variants = load_variants_from_yaml(args.variants_file)
    elif args.variants:
        ids = [v.strip() for v in args.variants.split(",")]
        variants = get_builtin(ids)
    else:
        variants = get_builtin()

    print(f"  {len(variants)} variant(s):")
    for v in variants:
        print(f"    {v.id:20s} — {v.name}")

    # 3. Run backtest
    print(f"\n[3/4] Running backtest (entry={args.entry})...")
    from level_tester.backtester.backtest_engine import BacktestEngine
    from level_tester.backtester.entry_types import EntryType
    from level_tester.infrastructure.binance import BinanceFuturesClient

    client = BinanceFuturesClient()
    engine = BacktestEngine(client)

    entry_type = EntryType(args.entry)

    def progress(current, total, symbol):
        pct = current / total * 100
        print(f"\r  [{current}/{total}] {symbol:12s} ... {pct:.0f}%", end="", flush=True)

    t0 = time.time()
    results = engine.run_all(
        signals, variants, entry_type,
        lookback_bars=args.lookback,
        lookforward_bars=args.lookforward,
        limit_offset_pct=args.limit_offset,
        confirmation_bars=args.confirmation_bars,
        confirmation_max_wait=args.confirmation_max_wait,
        progress_callback=progress,
    )
    elapsed = time.time() - t0
    print(f"\n  Done in {elapsed:.1f}s — {len(results)} trades generated")

    # 4. Compute metrics & generate report
    print("\n[4/4] Generating report...")
    from level_tester.backtester.metrics import compute_all_metrics
    from level_tester.backtester.report import generate_report

    # Group trades by variant
    by_variant: dict[str, list] = {}
    for r in results:
        if r.trade is not None:
            by_variant.setdefault(r.variant.id, []).append(r.trade)

    metrics = compute_all_metrics(by_variant)

    # Print summary
    print("\n" + "=" * 80)
    print(f"  BACKTEST RESULTS — Entry: {args.entry.upper()}")
    print(f"  Signals: {len(signals)} | Trades: {len(results)} | Closed: {sum(1 for r in results if r.trade and r.trade.status == 'closed')}")
    print("=" * 80)
    print(f"  {'Variant':20s} {'Trades':>6s} {'Winrate':>8s} {'PnL':>8s} {'PF':>6s} {'MaxDD':>8s}")
    print("  " + "-" * 62)
    for m in metrics:
        pf = f"{m.profit_factor:.2f}" if m.profit_factor != float("inf") else "∞"
        print(f"  {m.variant_name:20s} {m.total_trades:6d} {m.winrate:7.1f}% "
              f"{float(m.total_pnl):8.2f} {pf:>6s} {float(m.max_drawdown):8.2f}")
    print("=" * 80)

    # Generate HTML
    title = f"Backtest — {args.symbol or 'ALL'} — {args.period or 'ALL'} — {args.entry}"
    output = generate_report(results, metrics, args.output, title=title)
    print(f"\n  HTML report: {output.absolute()}")


if __name__ == "__main__":
    main()
