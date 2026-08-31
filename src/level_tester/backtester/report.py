"""Generate HTML backtest report with Lightweight Charts and Chart.js."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from level_tester.backtester.backtest_engine import TradeResult
from level_tester.backtester.metrics import VariantMetrics


COLORS = [
    "#2196F3", "#4CAF50", "#FF9800", "#E91E63", "#9C27B0",
    "#00BCD4", "#FF5722", "#607D8B", "#795548", "#CDDC39",
]


def generate_report(
    results: list[TradeResult],
    metrics: list[VariantMetrics],
    output_path: str | Path,
    title: str = "Backtest Report",
    candles_data: dict | None = None,
) -> Path:
    """Generate HTML report.

    Args:
        results: all TradeResult objects
        metrics: computed VariantMetrics per variant
        output_path: where to write the HTML file
        title: report title
        candles_data: optional {signal_id: [Candle, ...]} for candlestick charts

    Returns:
        Path to generated HTML file
    """
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    html = _build_html(results, metrics, title, candles_data)
    output.write_text(html, encoding="utf-8")
    return output


def _build_html(
    results: list[TradeResult],
    metrics: list[VariantMetrics],
    title: str,
    candles_data: dict | None,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Prepare data for JS
    equity_data = _build_equity_data(metrics)
    variant_ids = [m.variant_id for m in metrics]
    variant_names = [m.variant_name for m in metrics]
    trade_rows = _build_trade_rows(results)
    metrics_json = _build_metrics_json(metrics)
    candles_json = _build_candles_json(candles_data)

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="https://unpkg.com/lightweight-charts@5/dist/lightweight-charts.standalone.production.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: #0f1117; color: #e0e0e0; padding: 20px; }}
h1 {{ color: #2196F3; border-bottom: 2px solid #2196F3; padding-bottom: 10px; margin-bottom: 20px; }}
h2 {{ color: #4CAF50; margin: 20px 0 10px; font-size: 18px; }}
h3 {{ color: #888; margin: 15px 0 8px; font-size: 14px; }}
.tabs {{ display: flex; gap: 0; margin-bottom: 20px; border-bottom: 2px solid #333; }}
.tab {{ padding: 10px 20px; cursor: pointer; background: #1a1a2e; color: #888;
        border: 1px solid #333; border-bottom: none; border-radius: 6px 6px 0 0; font-size: 14px; }}
.tab.active {{ background: #0f1117; color: #2196F3; border-color: #2196F3; border-bottom: 2px solid #0f1117; margin-bottom: -2px; }}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}

/* Summary table */
table {{ width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 13px; }}
th {{ background: #1a1a2e; color: #2196F3; padding: 8px 12px; text-align: left; position: sticky; top: 0; }}
td {{ padding: 8px 12px; border-bottom: 1px solid #222; }}
tr:hover {{ background: #1a1a2e; }}
.pos {{ color: #4CAF50; }}
.neg {{ color: #f44336; }}
.zero {{ color: #888; }}
.best {{ background: #1b3a1b; }}

/* Equity chart */
.chart-container {{ height: 350px; background: #1a1a2e; border-radius: 8px; padding: 10px; margin: 10px 0; }}
.candle-chart {{ height: 400px; background: #1a1a2e; border-radius: 8px; padding: 10px; margin: 10px 0; }}

/* Trade navigation */
.trade-nav {{ display: flex; gap: 10px; align-items: center; margin: 10px 0; }}
.trade-nav button {{ padding: 6px 12px; background: #2196F3; color: white; border: none;
                      border-radius: 4px; cursor: pointer; font-size: 13px; }}
.trade-nav button:hover {{ background: #1976D2; }}
.trade-nav span {{ color: #888; font-size: 13px; }}

/* Filters */
.filters {{ display: flex; gap: 10px; margin: 10px 0; flex-wrap: wrap; }}
.filters select, .filters input {{ padding: 6px 10px; background: #1a1a2e; color: #e0e0e0;
                                    border: 1px solid #333; border-radius: 4px; font-size: 13px; }}
.filters label {{ font-size: 13px; color: #888; }}

/* Side breakdown */
.side-cards {{ display: flex; gap: 15px; margin: 10px 0; }}
.side-card {{ background: #1a1a2e; border: 1px solid #333; border-radius: 6px;
              padding: 12px 16px; min-width: 150px; }}
.side-card .label {{ color: #888; font-size: 12px; }}
.side-card .value {{ font-size: 20px; font-weight: bold; margin-top: 4px; }}
</style>
</head>
<body>
<h1>{title}</h1>
<p style="color:#888; margin-bottom:20px;">Generated: {now} | Signals tested: {len(set(r.signal.signal_id for r in results))} | Total trades: {len(results)}</p>

<div class="tabs">
  <div class="tab active" onclick="showTab('summary')">Summary</div>
  <div class="tab" onclick="showTab('equity')">Equity Curve</div>
  <div class="tab" onclick="showTab('candles')">Candlestick</div>
  <div class="tab" onclick="showTab('trades')">All Trades</div>
</div>

<!-- TAB: Summary -->
<div id="tab-summary" class="tab-content active">
  <h2>Variant Comparison</h2>
  <table id="summary-table">
    <thead>
      <tr>
        <th>Variant</th><th>Trades</th><th>Winrate</th><th>PnL</th>
        <th>PF</th><th>Max DD</th><th>Avg Win</th><th>Avg Loss</th>
        <th>Expectancy</th><th>Avg Bars</th>
      </tr>
    </thead>
    <tbody id="summary-body"></tbody>
  </table>

  <h3>By Side</h3>
  <div class="side-cards" id="side-cards"></div>

  <h3>By Symbol</h3>
  <table id="symbol-table">
    <thead><tr><th>Symbol</th><th>Trades</th><th>Winrate</th><th>PnL</th></tr></thead>
    <tbody id="symbol-body"></tbody>
  </table>
</div>

<!-- TAB: Equity Curve -->
<div id="tab-equity" class="tab-content">
  <h2>Equity Curve (all variants)</h2>
  <div class="chart-container"><canvas id="equityChart"></canvas></div>
  <h3>PnL by Variant</h3>
  <div class="chart-container" style="height:250px;"><canvas id="pnlBarChart"></canvas></div>
</div>

<!-- TAB: Candlestick -->
<div id="tab-candles" class="tab-content">
  <h2>Candlestick Chart</h2>
  <div class="filters">
    <label>Signal: <select id="signal-select"></select></label>
    <label>Variant: <select id="variant-select-candle"></select></label>
  </div>
  <div class="candle-chart" id="candle-chart-container"></div>
  <div class="trade-nav">
    <button onclick="prevTrade()">&larr; Prev</button>
    <span id="trade-info">-</span>
    <button onclick="nextTrade()">Next &rarr;</button>
  </div>
  <div id="trade-details" style="margin-top:10px; font-size:13px; color:#888;"></div>
</div>

<!-- TAB: Trades -->
<div id="tab-trades" class="tab-content">
  <h2>All Trades</h2>
  <div class="filters">
    <label>Symbol: <select id="filter-symbol"><option value="">All</option></select></label>
    <label>Side: <select id="filter-side"><option value="">All</option><option>LONG</option><option>SHORT</option></select></label>
    <label>Result: <select id="filter-result"><option value="">All</option><option value="win">Win</option><option value="loss">Loss</option></select></label>
    <label>Variant: <select id="filter-variant"><option value="">All</option></select></label>
  </div>
  <table>
    <thead>
      <tr>
        <th>#</th><th>Symbol</th><th>Side</th><th>Entry</th><th>Exit</th>
        <th>PnL</th><th>PnL %</th><th>Bars</th><th>Exit Reason</th><th>Variant</th>
      </tr>
    </thead>
    <tbody id="trades-body"></tbody>
  </table>
</div>

<script>
// === DATA ===
const equityData = {equity_data};
const variantIds = {json.dumps(variant_ids)};
const variantNames = {json.dumps(variant_names)};
const allTrades = {trade_rows};
const metricsData = {metrics_json};
const candlesData = {candles_json};
const colors = {json.dumps(COLORS)};

// === TABS ===
function showTab(name) {{
  document.querySelectorAll('.tab').forEach((t, i) => {{
    t.classList.toggle('active', t.textContent.toLowerCase().includes(name.substring(0,4)));
  }});
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  if (name === 'equity') renderEquityChart();
  if (name === 'candles') initCandleChart();
}}

// === SUMMARY TABLE ===
(function renderSummary() {{
  const tbody = document.getElementById('summary-body');
  const bestPf = Math.max(...metricsData.map(m => m.profit_factor === Infinity ? 999 : m.profit_factor));
  metricsData.forEach((m, i) => {{
    const tr = document.createElement('tr');
    const pf = m.profit_factor === Infinity ? '∞' : m.profit_factor.toFixed(2);
    const isBest = (m.profit_factor === Infinity ? 999 : m.profit_factor) === bestPf && m.total_trades > 0;
    if (isBest) tr.className = 'best';
    tr.innerHTML = `
      <td><b>${{m.variant_name}}</b></td>
      <td>${{m.total_trades}}</td>
      <td>${{m.winrate.toFixed(1)}}%</td>
      <td class="${{m.total_pnl >= 0 ? 'pos' : 'neg'}}">${{Number(m.total_pnl).toFixed(2)}}</td>
      <td>${{pf}}</td>
      <td class="neg">${{Number(m.max_drawdown).toFixed(2)}}</td>
      <td class="pos">${{Number(m.avg_win).toFixed(2)}}</td>
      <td class="neg">${{Number(m.avg_loss).toFixed(2)}}</td>
      <td>${{Number(m.expectancy).toFixed(2)}}</td>
      <td>${{m.avg_bars_held.toFixed(1)}}</td>`;
    tbody.appendChild(tr);
  }});

  // Side cards
  const sideDiv = document.getElementById('side-cards');
  const longT = allTrades.filter(t => t.side === 'LONG');
  const shortT = allTrades.filter(t => t.side === 'SHORT');
  const longWins = longT.filter(t => (t.pnl || 0) > 0).length;
  const shortWins = shortT.filter(t => (t.pnl || 0) > 0).length;
  sideDiv.innerHTML = `
    <div class="side-card"><div class="label">LONG</div><div class="value">${{longT.length}} trades</div>
      <div style="color:${{longWins/longT.length > 0.5 ? '#4CAF50' : '#f44336'}}">${{longT.length ? (longWins/longT.length*100).toFixed(1) : 0}}% winrate</div></div>
    <div class="side-card"><div class="label">SHORT</div><div class="value">${{shortT.length}} trades</div>
      <div style="color:${{shortWins/shortT.length > 0.5 ? '#4CAF50' : '#f44336'}}">${{shortT.length ? (shortWins/shortT.length*100).toFixed(1) : 0}}% winrate</div></div>`;

  // Symbol breakdown
  const symBody = document.getElementById('symbol-body');
  const bySym = {{}};
  allTrades.forEach(t => {{
    if (!bySym[t.symbol]) bySym[t.symbol] = {{trades:0, wins:0, pnl:0}};
    bySym[t.symbol].trades++;
    if ((t.pnl || 0) > 0) bySym[t.symbol].wins++;
    bySym[t.symbol].pnl += (t.pnl || 0);
  }});
  Object.entries(bySym).sort((a,b) => b[1].pnl - a[1].pnl).forEach(([sym, d]) => {{
    const tr = document.createElement('tr');
    tr.innerHTML = `<td><b>${{sym}}</b></td><td>${{d.trades}}</td>
      <td>${{d.trades ? (d.wins/d.trades*100).toFixed(1) : 0}}%</td>
      <td class="${{d.pnl >= 0 ? 'pos' : 'neg'}}">${{d.pnl.toFixed(2)}}</td>`;
    symBody.appendChild(tr);
  }});

  // Populate filter dropdowns
  const filterSym = document.getElementById('filter-symbol');
  const filterVar = document.getElementById('filter-variant');
  Object.keys(bySym).sort().forEach(s => {{
    const o = document.createElement('option'); o.value = s; o.textContent = s;
    filterSym.appendChild(o);
  }});
  variantNames.forEach((n, i) => {{
    const o = document.createElement('option'); o.value = variantIds[i]; o.textContent = n;
    filterVar.appendChild(o);
  }});
}})();

// === EQUITY CHART ===
let equityChartInstance = null;
function renderEquityChart() {{
  if (equityChartInstance) equityChartInstance.destroy();
  const ctx = document.getElementById('equityChart').getContext('2d');
  const datasets = variantIds.map((id, i) => ({{
    label: variantNames[i],
    data: equityData[id] || [],
    borderColor: colors[i % colors.length],
    backgroundColor: 'transparent',
    borderWidth: 2,
    pointRadius: 0,
    tension: 0.1,
  }}));
  equityChartInstance = new Chart(ctx, {{
    type: 'line',
    data: {{ labels: Array.from({{length: Math.max(...Object.values(equityData).map(a=>a.length), 1)}}, (_,i) => i), datasets }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ labels: {{ color: '#e0e0e0' }} }} }},
      scales: {{
        x: {{ display: false }},
        y: {{ title: {{ display: true, text: 'PnL', color: '#888' }}, ticks: {{ color: '#888' }}, grid: {{ color: '#222' }} }}
      }}
    }}
  }});

  // PnL bar chart
  const barCtx = document.getElementById('pnlBarChart').getContext('2d');
  new Chart(barCtx, {{
    type: 'bar',
    data: {{
      labels: variantNames,
      datasets: [{{ data: metricsData.map(m => Number(m.total_pnl)),
        backgroundColor: metricsData.map(m => m.total_pnl >= 0 ? '#4CAF50' : '#f44336') }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{
        x: {{ ticks: {{ color: '#888', maxRotation: 45 }} }},
        y: {{ ticks: {{ color: '#888' }}, grid: {{ color: '#222' }} }}
      }}
    }}
  }});
}}

// === TRADES TABLE ===
function renderTrades(trades) {{
  const tbody = document.getElementById('trades-body');
  tbody.innerHTML = '';
  trades.forEach((t, i) => {{
    const pnl = t.pnl || 0;
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${{i+1}}</td><td>${{t.symbol}}</td><td>${{t.side}}</td>
      <td>${{Number(t.entry_price).toFixed(6)}}</td>
      <td>${{t.exit_price ? Number(t.exit_price).toFixed(6) : '-'}}</td>
      <td class="${{pnl > 0 ? 'pos' : pnl < 0 ? 'neg' : 'zero'}}">${{pnl.toFixed(4)}}</td>
      <td class="${{t.pnl_pct > 0 ? 'pos' : t.pnl_pct < 0 ? 'neg' : 'zero'}}">${{(t.pnl_pct||0).toFixed(2)}}%</td>
      <td>${{t.bars_held}}</td>
      <td>${{t.exit_reason || '-'}}</td>
      <td>${{t.variant_name || t.variant_id}}</td>`;
    tbody.appendChild(tr);
  }});
}}
renderTrades(allTrades);

// Filters
function applyFilters() {{
  const sym = document.getElementById('filter-symbol').value;
  const side = document.getElementById('filter-side').value;
  const result = document.getElementById('filter-result').value;
  const varId = document.getElementById('filter-variant').value;
  let filtered = allTrades;
  if (sym) filtered = filtered.filter(t => t.symbol === sym);
  if (side) filtered = filtered.filter(t => t.side === side);
  if (result === 'win') filtered = filtered.filter(t => (t.pnl || 0) > 0);
  if (result === 'loss') filtered = filtered.filter(t => (t.pnl || 0) < 0);
  if (varId) filtered = filtered.filter(t => t.variant_id === varId);
  renderTrades(filtered);
}}
document.getElementById('filter-symbol').onchange = applyFilters;
document.getElementById('filter-side').onchange = applyFilters;
document.getElementById('filter-result').onchange = applyFilters;
document.getElementById('filter-variant').onchange = applyFilters;

// === CANDLESTICK CHART ===
let candleChart = null;
let candleSeries = null;
let currentTradeIdx = 0;
let filteredCandleTrades = [];

function initCandleChart() {{
  const sigSelect = document.getElementById('signal-select');
  const varSelect = document.getElementById('variant-select-candle');
  sigSelect.innerHTML = '';
  varSelect.innerHTML = '';
  const signals = [...new Set(allTrades.map(t => t.signal_id))];
  signals.forEach(s => {{
    const o = document.createElement('option'); o.value = s; o.textContent = s;
    sigSelect.appendChild(o);
  }});
  variantIds.forEach((id, i) => {{
    const o = document.createElement('option'); o.value = id; o.textContent = variantNames[i];
    varSelect.appendChild(o);
  }});
  sigSelect.onchange = updateCandleView;
  varSelect.onchange = updateCandleView;
  updateCandleView();
}}

function updateCandleView() {{
  const sigId = document.getElementById('signal-select').value;
  const varId = document.getElementById('variant-select-candle').value;
  filteredCandleTrades = allTrades.filter(t =>
    (!sigId || t.signal_id === sigId) && (!varId || t.variant_id === varId)
  );
  currentTradeIdx = 0;
  showCandleTrade();
}}

function showCandleTrade() {{
  if (!filteredCandleTrades.length) return;
  const t = filteredCandleTrades[currentTradeIdx];
  document.getElementById('trade-info').textContent =
    `Trade ${{currentTradeIdx+1}} / ${{filteredCandleTrades.length}} — ${{t.symbol}} ${{t.side}}`;

  // Get candles for this signal
  const candles = candlesData[t.signal_id];
  if (!candles || !candles.length) {{
    document.getElementById('trade-details').textContent = 'No candle data available for this signal.';
    return;
  }}

  // Find entry/exit indices
  const entryTime = t.entry_time;
  const exitTime = t.exit_time;

  // Render with Lightweight Charts
  const container = document.getElementById('candle-chart-container');
  container.innerHTML = '';
  candleChart = LightweightCharts.createChart(container, {{
    width: container.clientWidth,
    height: 400,
    layout: {{ background: {{ color: '#1a1a2e' }}, textColor: '#e0e0e0' }},
    grid: {{ vertLines: {{ color: '#222' }}, horzLines: {{ color: '#222' }} }},
    timeScale: {{ timeVisible: true, secondsVisible: false }},
  }});
  candleSeries = candleChart.addCandlestickSeries({{
    upColor: '#4CAF50', downColor: '#f44336',
    borderUpColor: '#4CAF50', borderDownColor: '#f44336',
    wickUpColor: '#4CAF50', wickDownColor: '#f44336',
  }});

  const chartData = candles.map(c => ({{
    time: Math.floor(new Date(c.time || c.open_time).getTime() / 1000),
    open: Number(c.open), high: Number(c.high), low: Number(c.low), close: Number(c.close),
  }})).sort((a,b) => a.time - b.time);

  candleSeries.setData(chartData);

  // Entry line
  if (t.entry_price) {{
    candleSeries.createPriceLine({{
      price: Number(t.entry_price),
      color: '#2196F3', lineWidth: 2, lineStyle: 0,
      title: 'Entry',
    }});
  }}
  // SL line
  if (t.stop_price) {{
    candleSeries.createPriceLine({{
      price: Number(t.stop_price),
      color: '#f44336', lineWidth: 1, lineStyle: 2,
      title: 'SL',
    }});
  }}
  // TP line
  if (t.take_price) {{
    candleSeries.createPriceLine({{
      price: Number(t.take_price),
      color: '#4CAF50', lineWidth: 1, lineStyle: 2,
      title: 'TP',
    }});
  }}

  candleChart.timeScale().fitContent();

  // Details
  const pnl = t.pnl || 0;
  document.getElementById('trade-details').innerHTML =
    `<b>Entry:</b> ${{Number(t.entry_price).toFixed(6)}} @ ${{t.entry_time || '-'}} | ` +
    `<b>Exit:</b> ${{t.exit_price ? Number(t.exit_price).toFixed(6) : '-'}} | ` +
    `<b>PnL:</b> <span class="${{pnl >= 0 ? 'pos' : 'neg'}}">${{pnl.toFixed(4)}}</span> | ` +
    `<b>Reason:</b> ${{t.exit_reason || '-'}} | <b>Bars:</b> ${{t.bars_held}}`;
}}

function prevTrade() {{ if (currentTradeIdx > 0) {{ currentTradeIdx--; showCandleTrade(); }} }}
function nextTrade() {{ if (currentTradeIdx < filteredCandleTrades.length - 1) {{ currentTradeIdx++; showCandleTrade(); }} }}
</script>
</body>
</html>"""


def _build_equity_data(metrics: list[VariantMetrics]) -> str:
    """Build JSON equity data per variant."""
    data = {}
    for m in metrics:
        data[m.variant_id] = m.equity_curve
    return json.dumps(data)


def _build_trade_rows(results: list[TradeResult]) -> str:
    """Build JSON array of trade objects for JS."""
    rows = []
    for r in results:
        if r.trade is None:
            continue
        t = r.trade
        rows.append({
            "signal_id": r.signal.signal_id,
            "symbol": r.signal.symbol,
            "side": t.side,
            "entry_price": str(t.entry_price),
            "entry_time": t.entry_time.isoformat() if t.entry_time else None,
            "exit_price": str(t.exit_price) if t.exit_price else None,
            "exit_time": t.exit_time.isoformat() if t.exit_time else None,
            "exit_reason": t.exit_reason,
            "pnl": str(t.pnl) if t.pnl is not None else "0",
            "pnl_pct": str(t.pnl_pct) if t.pnl_pct is not None else "0",
            "bars_held": t.bars_held,
            "stop_price": str(t.stop_price),
            "take_price": str(t.take_price),
            "variant_id": r.variant.id,
            "variant_name": r.variant.name,
        })
    return json.dumps(rows)


def _build_metrics_json(metrics: list[VariantMetrics]) -> str:
    """Build JSON metrics array for JS."""
    data = []
    for m in metrics:
        data.append({
            "variant_id": m.variant_id,
            "variant_name": m.variant_name,
            "total_trades": m.total_trades,
            "wins": m.wins,
            "losses": m.losses,
            "winrate": m.winrate,
            "total_pnl": str(m.total_pnl),
            "avg_pnl": str(m.avg_pnl),
            "avg_win": str(m.avg_win),
            "avg_loss": str(m.avg_loss),
            "profit_factor": m.profit_factor if m.profit_factor != float("inf") else "Infinity",
            "max_drawdown": str(m.max_drawdown),
            "expectancy": str(m.expectancy),
            "avg_bars_held": m.avg_bars_held,
            "long_trades": m.long_trades,
            "short_trades": m.short_trades,
            "long_winrate": m.long_winrate,
            "short_winrate": m.short_winrate,
        })
    return json.dumps(data)


def _build_candles_json(candles_data: dict | None) -> str:
    """Build JSON candles data for candlestick chart."""
    if not candles_data:
        return "{}"
    result = {}
    for sig_id, candles in candles_data.items():
        result[sig_id] = [
            {
                "time": c.open_time.isoformat(),
                "open": str(c.open),
                "high": str(c.high),
                "low": str(c.low),
                "close": str(c.close),
            }
            for c in candles
        ]
    return json.dumps(result)
