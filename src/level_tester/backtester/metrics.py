"""Compute metrics from backtest trade results."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from level_tester.backtester.trade_model import BacktestTrade


@dataclass(slots=True)
class VariantMetrics:
    """Aggregated metrics for one variant across all signals."""
    variant_id: str
    variant_name: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    winrate: float = 0.0
    total_pnl: Decimal = Decimal(0)
    avg_pnl: Decimal = Decimal(0)
    avg_win: Decimal = Decimal(0)
    avg_loss: Decimal = Decimal(0)
    profit_factor: float = 0.0
    max_drawdown: Decimal = Decimal(0)
    expectancy: Decimal = Decimal(0)
    avg_bars_held: float = 0.0
    # By side
    long_trades: int = 0
    short_trades: int = 0
    long_wins: int = 0
    short_wins: int = 0
    long_winrate: float = 0.0
    short_winrate: float = 0.0
    # Equity curve
    equity_curve: list[float] = field(default_factory=list)
    # Trades list
    trades: list[BacktestTrade] = field(default_factory=list)


def compute_metrics(variant_id: str, variant_name: str, trades: list[BacktestTrade]) -> VariantMetrics:
    """Compute aggregated metrics from a list of trades."""
    m = VariantMetrics(variant_id=variant_id, variant_name=variant_name)
    m.trades = trades

    if not trades:
        return m

    m.total_trades = len(trades)
    pnls: list[Decimal] = []
    wins_list: list[Decimal] = []
    losses_list: list[Decimal] = []
    bars: list[int] = []

    equity = 0.0
    equity_curve = [0.0]
    peak = 0.0
    max_dd = 0.0

    for t in trades:
        pnl = t.pnl or Decimal(0)
        pnl_f = float(pnl)
        pnls.append(pnl)
        bars.append(t.bars_held)

        # Side stats
        if t.side == "LONG":
            m.long_trades += 1
        else:
            m.short_trades += 1

        if pnl > 0:
            m.wins += 1
            wins_list.append(pnl)
            if t.side == "LONG":
                m.long_wins += 1
            else:
                m.short_wins += 1
        elif pnl < 0:
            m.losses += 1
            losses_list.append(pnl)

        # Equity curve
        equity += pnl_f
        equity_curve.append(equity)
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    m.equity_curve = equity_curve
    m.total_pnl = sum(pnls)
    m.avg_pnl = m.total_pnl / m.total_trades if m.total_trades else Decimal(0)
    m.avg_win = sum(wins_list) / len(wins_list) if wins_list else Decimal(0)
    m.avg_loss = sum(losses_list) / len(losses_list) if losses_list else Decimal(0)
    m.winrate = m.wins / m.total_trades * 100 if m.total_trades else 0.0
    m.max_drawdown = Decimal(str(max_dd))
    m.avg_bars_held = sum(bars) / len(bars) if bars else 0.0

    # Profit Factor
    abs_losses = sum(abs(x) for x in losses_list)
    m.profit_factor = float(sum(wins_list) / abs_losses) if abs_losses > 0 else float("inf")

    # Expectancy
    avg_win_f = float(m.avg_win)
    avg_loss_f = float(m.avg_loss)
    wr = m.winrate / 100
    m.expectancy = Decimal(str(wr * avg_win_f + (1 - wr) * avg_loss_f))

    # Side winrates
    m.long_winrate = m.long_wins / m.long_trades * 100 if m.long_trades else 0.0
    m.short_winrate = m.short_wins / m.short_trades * 100 if m.short_trades else 0.0

    return m


def compute_all_metrics(
    results_by_variant: dict[str, list[BacktestTrade]],
) -> list[VariantMetrics]:
    """Compute metrics for each variant."""
    metrics = []
    for variant_id, trades in results_by_variant.items():
        name = trades[0].id.rsplit("_", 1)[0] if trades else variant_id
        metrics.append(compute_metrics(variant_id, name, trades))
    # Sort by profit factor descending
    metrics.sort(key=lambda m: m.profit_factor, reverse=True)
    return metrics
