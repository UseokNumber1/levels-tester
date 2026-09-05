// visual.js - Isolated visual mode for trade replay from backtest
'use strict';

// === Constants ===
const RIGHT_OFFSET_BARS = 5;
const $ = (id) => document.getElementById(id);

// === State ===
const state = {
  runId: null,
  chart: null,
  candleSeries: null,
  allCandles: [],
  animTimer: null,
  animRunning: false,
  lastStatus: null,
  drawnTime: undefined,
  chartHasData: false,
  levelLines: [],
  totalBars: 0,
  displayFromMs: null,
  detailChart: null,
  detailSeries: null,
  detailActive: false,
  detailEnabled: false,
  detailPrevMs: null,
  detailDrawnTime: undefined,
  detailAnimSeq: 0,
  detailCount: 0,
  detailBars: [],
  detailLevelLines: [],
  tradeLevelLines: [],
  trailingStopLine: null,
  trailingStopActivated: false,
  highestPriceSinceEntry: 0,
  lowestPriceSinceEntry: 0,
  pricePrecision: 2,
  tickSize: '0.01',
  tradeVisualParams: null,
  pivotMarkers: null,
};

// === Utility Functions ===
function stepDelay() {
  const speed = Number($('speed').value) || 1;
  return Math.max(8, Math.round(1000 / speed));
}

function scrollChartToRight(chart) {
  if (!chart) return;
  const timeScale = chart.timeScale();
  if (typeof timeScale.scrollToPosition === 'function') {
    timeScale.scrollToPosition(5, false);
  } else if (typeof timeScale.fitContent === 'function') {
    timeScale.fitContent();
  }
}

function enablePriceAutoScale(series) {
  if (!series) return;
  const priceScale = series.priceScale();
  if (typeof priceScale.setAutoScale === 'function') {
    priceScale.setAutoScale(true);
  } else {
    priceScale.applyOptions({ autoScale: true });
  }
}

function fmtPrice(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return n.toFixed(state.pricePrecision);
}

function isConfirmedLevel(level) {
  return !['created', 'broken', 'expired'].includes(level.state);
}

function utcFormat(value) {
  const d = new Date(value);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

async function request(url, options = {}) {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || 'Request failed');
  return data;
}

const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

// === Parse URL Parameters ===
function parseTradeVisualParams() {
  const urlParams = new URLSearchParams(window.location.search);
  if (!urlParams.has('symbol')) return null;
  
  const params = {
    symbol: urlParams.get('symbol'),
    display_from: urlParams.get('display_from') || '',
    side: urlParams.get('side') || 'LONG',
    entry_price: Number(urlParams.get('entry_price')) || 0,
    stop_price: Number(urlParams.get('stop_price')) || 0,
    take_price: Number(urlParams.get('take_price')) || 0,
    variant_id: urlParams.get('variant_id') || '',
    variant_name: urlParams.get('variant_name') || '',
    trailing: {
      trailing_stop_pct: Number(urlParams.get('trailing_stop_pct')) || 0,
      trailing_activation_pct: Number(urlParams.get('trailing_activation_pct')) || 0,
      trailing_update_threshold_pct: Number(urlParams.get('trailing_update_threshold_pct')) || 0,
      trailing_tp_only: urlParams.get('trailing_tp_only') === 'true',
    },
    breakeven: {
      breakeven_trigger_pct: Number(urlParams.get('breakeven_trigger_pct')) || 0,
      breakeven_lock_pct: Number(urlParams.get('breakeven_lock_pct')) || 0,
    },
    partial: {
      partial_close_pct: Number(urlParams.get('partial_close_pct')) || 0,
      partial_close_rr: Number(urlParams.get('partial_close_rr')) || 0,
    },
    confirmation_method: urlParams.get('confirmation_method') || '',
    detail_tf: urlParams.get('detail_tf') || '5m',
  };
  return params;
}

// === Server Status ===
async function checkServer() {
  try {
    const status = await request('/api/status');
    const connected = status.backend === 'connected' && status.database === 'connected' && status.database_schema === 'valid';
    $('server-dot').className = `dot ${connected ? 'ok' : 'bad'}`;
    $('server-status').textContent = `SERVER ${connected ? 'CONNECTED' : 'DEGRADED'} · SQL ${status.database_timezone} · BINANCE ${status.binance.toUpperCase()}`;
  } catch (_) {
    $('server-dot').className = 'dot bad';
    $('server-status').textContent = 'SERVER DISCONNECTED';
  }
}

// === Trade Info Banner ===
function showTradeInfoBanner(params) {
  const banner = $('trade-info');
  if (!banner) return;
  banner.classList.remove('hidden');
  
  const symbolEl = $('trade-symbol');
  const sideEl = $('trade-side');
  const entryEl = $('trade-entry');
  const slEl = $('trade-sl');
  const tpEl = $('trade-tp');
  const methodEl = $('trade-method');
  const variantEl = $('trade-variant');
  
  if (symbolEl) symbolEl.textContent = params.symbol;
  if (sideEl) {
    sideEl.textContent = params.side;
    sideEl.className = 'trade-side ' + (params.side === 'LONG' ? 'pos' : 'neg');
  }
  if (entryEl) entryEl.textContent = `Entry: ${params.entry_price}`;
  if (slEl) slEl.textContent = `SL: ${params.stop_price}`;
  if (tpEl) tpEl.textContent = `TP: ${params.take_price}`;
  if (methodEl && params.confirmation_method) methodEl.textContent = params.confirmation_method;
  if (variantEl) variantEl.textContent = params.variant_name || params.variant_id;
}

// === Chart Initialization ===
function initChart() {
  if (state.chart) return;
  if (!window.LightweightCharts || !$('chart')) return;
  state.chart = LightweightCharts.createChart($('chart'), {
    autoSize: true,
    layout: { background: { color: '#fffdf8' }, textColor: '#71808a' },
    grid: { vertLines: { color: '#eeeae1' }, horzLines: { color: '#eeeae1' } },
    timeScale: { timeVisible: true, rightOffset: 5, barSpacing: 6 },
  });
  state.candleSeries = state.chart.addSeries(LightweightCharts.CandlestickSeries, {
    upColor: '#198754', downColor: '#ee6c4d',
    borderVisible: false,
    wickUpColor: '#198754', wickDownColor: '#ee6c4d',
    priceFormat: { type: 'price', precision: state.pricePrecision, minMove: Number(state.tickSize) },
  });
}

function initDetailChart() {
  if (state.detailChart) return;
  const container = $('detail-chart');
  if (!window.LightweightCharts || !container) return;
  state.detailChart = LightweightCharts.createChart(container, {
    autoSize: true,
    layout: { background: { color: '#fffdf8' }, textColor: '#71808a' },
    grid: { vertLines: { color: '#eeeae1' }, horzLines: { color: '#eeeae1' } },
    timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 5, barSpacing: 6 },
  });
  state.detailSeries = state.detailChart.addSeries(LightweightCharts.CandlestickSeries, {
    upColor: '#198754', downColor: '#ee6c4d',
    borderVisible: false,
    wickUpColor: '#198754', wickDownColor: '#ee6c4d',
    priceFormat: { type: 'price', precision: state.pricePrecision, minMove: Number(state.tickSize) },
  });
}

function destroyDetailChart() {
  if (state.detailChart) {
    try {
      if (typeof state.detailChart.remove === 'function') {
        state.detailChart.remove();
      }
    } catch (error) {
      console.warn('detail chart remove failed:', error);
    }
    state.detailChart = null;
    state.detailSeries = null;
  }
}

function resetDetailChart() {
  state.detailActive = false;
  state.detailEnabled = false;
  state.detailPrevMs = null;
  state.detailDrawnTime = undefined;
  state.detailCount = 0;
  state.detailBars = [];
  clearDetailLevelLines();
  destroyDetailChart();
  const container = $('detail-chart');
  container.classList.add('detail-waiting');
  container.textContent = 'Chart opens on level touch.';
  $('detail-status').textContent = 'waiting for touch';
}

function openDetailPanel() {
  if (state.detailActive) return;
  state.detailActive = true;
  const container = $('detail-chart');
  container.classList.remove('detail-waiting');
  container.textContent = '';
  initDetailChart();
  $('detail-status').textContent = 'synced with H1';
}

// === Level Lines ===
function clearLevelLines() {
  if (!state.candleSeries) return;
  state.levelLines.forEach(line => state.candleSeries.removePriceLine(line));
  state.levelLines = [];
}

function clearDetailLevelLines() {
  if (!state.detailSeries) return;
  state.detailLevelLines.forEach(line => state.detailSeries.removePriceLine(line));
  state.detailLevelLines = [];
}

function clearTradeLevelLines() {
  if (!state.candleSeries || !state.tradeLevelLines) return;
  state.tradeLevelLines.forEach(line => state.candleSeries.removePriceLine(line));
  state.tradeLevelLines = [];
}

function renderTradeLevels(params) {
  if (!params || !state.candleSeries) return;
  clearTradeLevelLines();
  
  const isLong = params.side === 'LONG';
  const colorEntry = '#4CAF50';
  const colorSL = '#f44336';
  const colorTP = '#2196F3';
  const colorTrail = '#FF9800';
  const colorBE = '#FFEB3B';
  
  // Entry line
  state.tradeLevelLines.push(
    state.candleSeries.createPriceLine({
      price: params.entry_price,
      color: colorEntry,
      lineWidth: 2,
      lineStyle: 0,
      axisLabelVisible: true,
      title: 'Entry',
    })
  );
  
  // Stop Loss
  state.tradeLevelLines.push(
    state.candleSeries.createPriceLine({
      price: params.stop_price,
      color: colorSL,
      lineWidth: 2,
      lineStyle: 0,
      axisLabelVisible: true,
      title: 'SL',
    })
  );
  
  // Take Profit
  state.tradeLevelLines.push(
    state.candleSeries.createPriceLine({
      price: params.take_price,
      color: colorTP,
      lineWidth: 2,
      lineStyle: 0,
      axisLabelVisible: true,
      title: 'TP',
    })
  );
  
  // Trailing Stop activation
  if (params.trailing?.trailing_stop_pct && params.trailing?.trailing_activation_pct) {
    const trailActivation = isLong
      ? params.entry_price * (1 + params.trailing.trailing_activation_pct / 100)
      : params.entry_price * (1 - params.trailing.trailing_activation_pct / 100);
    state.tradeLevelLines.push(
      state.candleSeries.createPriceLine({
        price: trailActivation,
        color: colorTrail,
        lineWidth: 1,
        lineStyle: 3,
        axisLabelVisible: true,
        title: 'Trail Activate',
      })
    );
  }
  
  // Breakeven
  if (params.breakeven?.breakeven_trigger_pct && params.breakeven?.breakeven_lock_pct) {
    const beLevel = isLong
      ? params.entry_price * (1 + params.breakeven.breakeven_lock_pct / 100)
      : params.entry_price * (1 - params.breakeven.breakeven_lock_pct / 100);
    state.tradeLevelLines.push(
      state.candleSeries.createPriceLine({
        price: beLevel,
        color: colorBE,
        lineWidth: 1,
        lineStyle: 3,
        axisLabelVisible: true,
        title: 'BE',
      })
    );
  }

  // Reset trailing stop tracking
  clearTrailingStopLine();
  state.highestPriceSinceEntry = Number(params.entry_price) || 0;
  state.lowestPriceSinceEntry = Number(params.entry_price) || 0;
}

function clearTrailingStopLine() {
  if (state.trailingStopLine && state.candleSeries) {
    state.candleSeries.removePriceLine(state.trailingStopLine);
    state.trailingStopLine = null;
  }
  state.trailingStopActivated = false;
}

function updateTrailingStop(params, currentPrice) {
  if (!params || !state.candleSeries) return;
  if (!params.trailing?.trailing_stop_pct || !params.trailing?.trailing_activation_pct) return;

  const isLong = params.side === 'LONG';
  const stopPct = params.trailing.trailing_stop_pct;

  // Track highest/lowest since entry
  if (isLong) {
    if (currentPrice > state.highestPriceSinceEntry) {
      state.highestPriceSinceEntry = currentPrice;
    }
  } else {
    if (state.lowestPriceSinceEntry === 0 || currentPrice < state.lowestPriceSinceEntry) {
      state.lowestPriceSinceEntry = currentPrice;
    }
  }

  // Check activation threshold
  const activationPct = params.trailing.trailing_activation_pct;
  const activationPrice = isLong
    ? params.entry_price * (1 + activationPct / 100)
    : params.entry_price * (1 - activationPct / 100);
  const isActivated = isLong
    ? currentPrice >= activationPrice
    : currentPrice <= activationPrice;
  if (!isActivated) return;

  // Compute current trailing stop price based on best price since entry
  const refPrice = isLong ? state.highestPriceSinceEntry : state.lowestPriceSinceEntry;
  let newStopPrice = isLong
    ? refPrice * (1 - stopPct / 100)
    : refPrice * (1 + stopPct / 100);
  // Don't let trailing stop cross the initial SL (profit protection)
  if (isLong && newStopPrice < params.stop_price) newStopPrice = params.stop_price;
  if (!isLong && newStopPrice > params.stop_price) newStopPrice = params.stop_price;

  if (!state.trailingStopLine) {
    state.trailingStopLine = state.candleSeries.createPriceLine({
      price: newStopPrice,
      color: '#FF9800',
      lineWidth: 2,
      lineStyle: 2,
      axisLabelVisible: true,
      title: 'Trailing SL',
    });
    state.trailingStopActivated = true;
  } else {
    const currentStop = state.trailingStopLine.options().price;
    const shouldUpdate = isLong ? newStopPrice > currentStop : newStopPrice < currentStop;
    if (shouldUpdate) {
      state.trailingStopLine.applyOptions({ price: newStopPrice });
    }
  }
}

function renderLevelLines(levels) {
  clearLevelLines();
  if (!state.candleSeries) return;
  state.levelLines = levels.flatMap(level => {
    const isSupport = level.side === 'support';
    const color = isSupport ? '#2166f3' : '#ee6c4d';
    const edgeColor = isSupport ? '#8eaff8' : '#f5a18d';
    const touched = level.state === 'touched';
    const title = `${level.side[0].toUpperCase()} ${level.state} ${level.touch_count}x`;
    return [
      state.candleSeries.createPriceLine({
        price: Number(level.zone_low),
        color: edgeColor,
        lineWidth: 1,
        lineStyle: 1,
        axisLabelVisible: false,
        title: `${level.side} zone low`,
      }),
      state.candleSeries.createPriceLine({
        price: Number(level.price),
        color,
        lineWidth: touched ? 2 : 1,
        lineStyle: touched ? 0 : 2,
        axisLabelVisible: true,
        title,
      }),
      state.candleSeries.createPriceLine({
        price: Number(level.zone_high),
        color: edgeColor,
        lineWidth: 1,
        lineStyle: 1,
        axisLabelVisible: false,
        title: `${level.side} zone high`,
      }),
    ];
  });
}

function renderDetailLevelLines(levels) {
  clearDetailLevelLines();
  if (!state.detailSeries || !state.detailActive) return;
  state.detailLevelLines = levels.flatMap(level => {
    const isSupport = level.side === 'support';
    const color = isSupport ? '#2166f3' : '#ee6c4d';
    return [
      state.detailSeries.createPriceLine({
        price: Number(level.zone_low),
        color,
        lineWidth: 1,
        lineStyle: 3,
        axisLabelVisible: false,
        title: '',
      }),
      state.detailSeries.createPriceLine({
        price: Number(level.price),
        color,
        lineWidth: level.state === 'touched' ? 2 : 1,
        lineStyle: 0,
        axisLabelVisible: true,
        title: `${level.side[0].toUpperCase()} ${fmtPrice(level.price)}`,
      }),
      state.detailSeries.createPriceLine({
        price: Number(level.zone_high),
        color,
        lineWidth: 1,
        lineStyle: 3,
        axisLabelVisible: false,
        title: '',
      }),
    ];
  });
}

function setPivotMarkers(markers) {
  if (!state.candleSeries) return;
  if (!state.pivotMarkers) {
    state.pivotMarkers = LightweightCharts.createSeriesMarkers(state.candleSeries, []);
  }
  state.pivotMarkers.setMarkers(markers);
}

// === Chart Synchronization ===
function syncChartToCursor(snapshot) {
  initChart();
  if (!state.chart) return;

  const cursorTime = snapshot.cursor
    ? Math.floor(new Date(snapshot.cursor.bar_time).getTime() / 1000)
    : null;

  if (cursorTime === null) {
    if (state.chartHasData) {
      state.candleSeries.setData([]);
      state.chartHasData = false;
    }
    state.drawnTime = undefined;
    clearLevelLines();
    setPivotMarkers([]);
    return;
  }

  const visibleCandles = snapshot.master_candles
    .filter(c => Math.floor(new Date(c.open_time).getTime() / 1000) <= cursorTime);

  const bars = visibleCandles.map(c => ({
    time: Math.floor(new Date(c.open_time).getTime() / 1000),
    open: Number(c.open), high: Number(c.high),
    low: Number(c.low), close: Number(c.close),
  }));

  const lastTime = bars.length > 0 ? bars[bars.length - 1].time : null;
  const initialLoad = state.drawnTime === undefined || lastTime === null;

  if (initialLoad) {
    state.candleSeries.setData(bars);
    state.drawnTime = lastTime;
  } else {
    for (const bar of bars) {
      if (bar.time > state.drawnTime) {
        state.candleSeries.update(bar);
      }
    }
    if (bars.length > 0) {
      state.drawnTime = bars[bars.length - 1].time;
    }
  }

  enablePriceAutoScale(state.candleSeries);
  scrollChartToRight(state.chart);

  state.chartHasData = bars.length > 0;

  renderLevelLines(snapshot.levels.filter(isConfirmedLevel));
  renderDetailLevelLines(snapshot.levels.filter(isConfirmedLevel));
  
  // Render trade visual levels
  if (state.tradeVisualParams) {
    renderTradeLevels(state.tradeVisualParams);

    // Update trailing stop on every candle (only after entry price reached)
    if (snapshot.cursor) {
      const currentCandle = snapshot.master_candles.find(c => c.close_time === snapshot.cursor.bar_time);
      if (currentCandle) {
        const currentPrice = Number(currentCandle.close);
        const entryPrice = Number(state.tradeVisualParams.entry_price);
        // Track prices only after entry is reached
        if (currentPrice >= entryPrice || currentPrice <= entryPrice) {
          updateTrailingStop(state.tradeVisualParams, currentPrice);
        }
      }
    }

    // Auto-open detail chart when price approaches entry ±0.5%
    if (snapshot.cursor && !state.detailActive) {
      const cursorClose = Number(snapshot.master_candles.find(c => c.close_time === snapshot.cursor.bar_time)?.close || 0);
      const entryPrice = state.tradeVisualParams.entry_price;
      if (entryPrice > 0 && cursorClose > 0) {
        const distancePct = Math.abs(cursorClose - entryPrice) / entryPrice * 100;
        if (distancePct <= 0.5) {
          state.detailActive = true;
          initDetailChart();
          syncDetail(snapshot);
        }
      }
    }
  }
  
  setPivotMarkers(snapshot.pivots.map(pivot => ({
    time: Math.floor(new Date(pivot.pivot_time).getTime() / 1000),
    position: pivot.kind === 'high' ? 'aboveBar' : 'belowBar',
    color: pivot.kind === 'high' ? '#ee6c4d' : '#2166f3',
    shape: pivot.kind === 'high' ? 'arrowDown' : 'arrowUp',
    text: pivot.kind,
  })));
}

// === Detail Candles ===
async function syncDetail(snapshot) {
  if (!snapshot.cursor) return;
  const cursorMs = new Date(snapshot.cursor.bar_time).getTime();
  state.detailEnabled = true;
  openDetailPanel();
  let startMs = state.detailPrevMs ?? cursorMs - 3600 * 1000;
  if (startMs >= cursorMs) startMs = cursorMs - 3600 * 1000;
  
  try {
    const params = new URLSearchParams({
      start: new Date(startMs).toISOString(),
      end: new Date(cursorMs).toISOString(),
    });
    const data = await request(`/api/runs/${state.runId}/detail?${params}`);
    state.detailPrevMs = cursorMs;
    await animateDetailCandles(data.detail_candles || [], startMs, cursorMs, snapshot);
  } catch (error) {
    console.error('detail load failed:', error);
    $('detail-status').textContent = `detail error: ${error && error.message ? error.message : 'unavailable'}`;
  }
}

async function animateDetailCandles(candles, startMs, endMs, snapshot) {
  state.detailAnimSeq++;
  initDetailChart();
  if (!state.detailSeries) return;
  const animSeq = state.detailAnimSeq;
  const detailTf = $('detail-tf').value.toUpperCase();
  const bars = candles
    .filter(c => {
      const t = new Date(c.open_time).getTime();
      return t > startMs && t <= endMs;
    })
    .map(c => ({
      time: Math.floor(new Date(c.open_time).getTime() / 1000),
      open: Number(c.open), high: Number(c.high),
      low: Number(c.low), close: Number(c.close),
    }))
    .sort((a, b) => a.time - b.time);
  let lastTime = typeof state.detailDrawnTime === 'number' ? state.detailDrawnTime : undefined;
  const newBars = bars.filter(bar => lastTime === undefined || bar.time > lastTime);
  $('detail-status').textContent = `${state.detailCount}/${state.detailCount + newBars.length} ${detailTf} · ${utcFormat(endMs)} UTC`;
  if (!newBars.length) return;
  
  const delay = Math.max(30, Math.round(1000 / (Number($('speed').value) || 1)));
  const initialLoad = state.detailDrawnTime === undefined;
  if (initialLoad) state.detailBars = [];

  for (let i = 0; i < newBars.length; i++) {
    if (animSeq !== state.detailAnimSeq || !state.detailActive) return;
    const bar = newBars[i];
    const nextBars = [...state.detailBars, bar];
    try {
      state.detailSeries.setData(nextBars);
    } catch (error) {
      console.error('detail setData failed:', bar, error);
      $('detail-status').textContent = `draw error at ${utcFormat(bar.time * 1000)} UTC`;
      return;
    }
    state.detailBars = nextBars;
    state.detailDrawnTime = bar.time;
    state.detailCount += 1;
    $('detail-status').textContent = `${state.detailCount} ${detailTf} · ${utcFormat(endMs)} UTC`;
    if (i < newBars.length - 1) {
      await sleep(delay);
    }
  }
  
  enablePriceAutoScale(state.detailSeries);
  scrollChartToRight(state.detailChart);
}

// === Render Snapshot ===
function render(snapshot, deferChart = false) {
  state.runId = snapshot.run_id;
  if (Number.isFinite(Number(snapshot.speed))) {
    const speedEl = $('speed');
    if (speedEl) speedEl.value = String(snapshot.speed);
    const speedValEl = $('speed-value');
    if (speedValEl) speedValEl.textContent = `${snapshot.speed}×`;
  }
  const isLoading = snapshot.status === 'loading';
  const wasLoading = state.lastStatus === 'loading';
  state.lastStatus = snapshot.status;

  if (snapshot.price_precision != null) state.pricePrecision = snapshot.price_precision;
  if (snapshot.tick_size) state.tickSize = snapshot.tick_size;

  if (state.candleSeries) {
    state.candleSeries.applyOptions({
      priceFormat: { type: 'price', precision: state.pricePrecision, minMove: Number(state.tickSize) },
    });
  }
  if (state.detailSeries) {
    state.detailSeries.applyOptions({
      priceFormat: { type: 'price', precision: state.pricePrecision, minMove: Number(state.tickSize) },
    });
  }

  if (isLoading) {
    $('loading-bar').classList.remove('hidden');
    $('loading-fill').style.width = `${snapshot.progress || 0}%`;
    $('loading-percent').textContent = `${snapshot.progress || 0}%`;
    $('loading-stage').textContent = snapshot.loading_stage || 'Loading...';
    $('run-status').textContent = `LOADING ${snapshot.progress || 0}%`;
  } else {
    $('loading-bar').classList.add('hidden');
    $('run-status').textContent = (snapshot.status || 'unknown').toUpperCase();
  }

  $('cursor').textContent = snapshot.cursor
    ? `${utcFormat(snapshot.cursor.bar_time)} UTC`
    : 'not started';
  $('bar-count').textContent = `${snapshot.master_candles.length} bars`;
  if (snapshot.total_candles) state.totalBars = snapshot.total_candles;
  const sequence = snapshot.cursor ? snapshot.cursor.sequence : 0;
  const percent = state.totalBars ? Math.min(100, Math.round((sequence / state.totalBars) * 100)) : 0;
  $('replay-fill').style.width = `${percent}%`;
  $('replay-text').textContent = `${sequence} / ${state.totalBars} (${percent}%)`;
  const confirmedLevels = snapshot.levels.filter(isConfirmedLevel);
  $('level-count').textContent = confirmedLevels.length;

  $('levels').classList.toggle('empty', !confirmedLevels.length);
  $('levels').innerHTML = confirmedLevels.length
    ? confirmedLevels.map(level => `
    <div class="level"><div class="level-title ${level.side}"><span>${level.side}</span><span>${fmtPrice(level.price)}</span></div>
    <div class="level-meta">${level.state} · ${level.touch_count} touches · zone ${fmtPrice(level.zone_low)}–${fmtPrice(level.zone_high)}</div></div>`).join('')
    : 'No levels confirmed yet.';

  if (!isLoading && snapshot.master_candles.length > 0 && !deferChart) {
    state.allCandles = snapshot.master_candles;
    syncChartToCursor(snapshot);
  }
}

// === Animation Control ===
function showHint(text, isFinish = false) {
  const hint = $('replay-hint');
  hint.textContent = text;
  hint.classList.toggle('finish', isFinish);
}

function hideHint() {
  const hint = $('replay-hint');
  hint.textContent = '';
  hint.classList.remove('finish');
}

function stopAnimation() {
  state.animRunning = false;
  if (state.animTimer) {
    clearTimeout(state.animTimer);
    state.animTimer = null;
  }
}

function startAutoPlay() {
  stopAnimation();
  state.animRunning = true;
  autoPlayStep();
}

async function autoPlayStep() {
  if (!state.animRunning || !state.runId) return;
  try {
    const snapshot = await request(`/api/runs/${state.runId}/step`, { method: 'POST' });
    render(snapshot);
    if (state.detailEnabled) {
      await syncDetail(snapshot);
    }
    
    // Check if price reached entry (visual mode)
    if (state.tradeVisualParams && snapshot.cursor && !state.detailEnabled) {
      const entry = Number(state.tradeVisualParams.entry_price);
      const curCandle = snapshot.master_candles.find(item => item.close_time === snapshot.cursor.bar_time);
      if (curCandle && entry) {
        const low = Number(curCandle.low), high = Number(curCandle.high);
        const inCandle = low <= entry && entry <= high;
        const bandPct = 0.003;
        const nearBand = Math.min(Math.abs(low-entry), Math.abs(high-entry)) / entry < bandPct;
        if (inCandle || nearBand) {
          state.detailEnabled = true;
          state.detailPrevMs = new Date(curCandle.open_time).getTime();
          await syncDetail(snapshot);
          showHint('⏸ Пауза — цена коснулась уровня входа');
          stopAnimation();
          return;
        }
      }
    }
    
    if (snapshot.status === 'completed') {
      stopAnimation();
      showHint('✔ Финиш — реплей достиг последней свечи', true);
      return;
    }
    if (snapshot.status === 'failed' || snapshot.status === 'cancelled') {
      stopAnimation();
      return;
    }
    if (!state.animRunning) return;
    const beforeDisplay = snapshot.cursor && state.displayFromMs
      && new Date(snapshot.cursor.bar_time).getTime() < state.displayFromMs;
    state.animTimer = setTimeout(autoPlayStep, beforeDisplay ? 0 : stepDelay());
  } catch (error) {
    console.error('autoPlayStep error:', error);
    stopAnimation();
  }
}

// === Command Handler ===
async function command(name) {
  if (!state.runId) return;
  try {
    if (name === 'play') {
      hideHint();
      startAutoPlay();
      return;
    }
    if (name === 'pause') {
      stopAnimation();
      showHint('⏸ Пауза — нажмите Play или Step для продолжения');
    } else {
      hideHint();
      if (name === 'step' || name === 'reset' || name === 'cancel') {
        stopAnimation();
      }
    }
    const snapshot = await request(`/api/runs/${state.runId}/${name}`, { method: 'POST' });
    render(snapshot, name === 'step');
    if (name === 'step' && snapshot.cursor) {
      await syncDetail(snapshot);
    }
    if (name === 'reset') resetDetailChart();
  } catch (error) {
    alert(error.message);
  }
}

// === Poll Run Status ===
async function pollRun(runId) {
  for (;;) {
    await new Promise(resolve => setTimeout(resolve, 500));
    const snapshot = await request(`/api/runs/${runId}`);
    render(snapshot);
    if (snapshot.status !== 'loading') break;
  }
}

// === Auto Start Replay (visual mode entry point) ===
async function autoStartReplay(params) {
  try {
    const dateStr = (params.display_from || new Date().toISOString().slice(0, 10)) + 'T00:00:00Z';
    state.displayFromMs = Date.parse(dateStr);

    const methodMap = {
      'Touch': { methods: ['touch'], required_bars: 1 },
      '1 bar': { methods: ['consecutive'], required_bars: 1 },
      '2 bars': { methods: ['consecutive'], required_bars: 2 },
    };
    const methodKey = params.confirmation_method || '';
    const methodConfig = methodMap[methodKey] || { methods: ['consecutive'], required_bars: 2 };

    const snapshot = await request('/api/runs', {
      method: 'POST',
      body: JSON.stringify({
        symbol: params.symbol,
        display_from: dateStr,
        detail_timeframe: params.detail_tf || '5m',
        confirmation_methods: methodConfig.methods,
        confirmation_required_bars: methodConfig.required_bars,
        confirmation_max_wait_bars: 15,
      }),
    });
    render(snapshot);
    await pollRun(snapshot.run_id);
  } catch (error) {
    console.error('Failed to start visual replay:', error);
    const loadingStage = $('loading-stage');
    if (loadingStage) loadingStage.textContent = 'Error: ' + error.message;
    const loadingBar = $('loading-bar');
    if (loadingBar) loadingBar.classList.remove('hidden');
    const loadingFill = $('loading-fill');
    if (loadingFill) loadingFill.style.width = '0%';
  }
}

// === Init Visual Mode ===
async function initVisualMode(params) {
  showTradeInfoBanner(params);
  await autoStartReplay(params);
}

// === Event Bindings ===
document.querySelectorAll('[data-command]').forEach(button => {
  button.onclick = () => command(button.dataset.command);
});

const speedEl = $('speed');
if (speedEl) {
  speedEl.oninput = () => {
    $('speed-value').textContent = `${speedEl.value}×`;
  };
  speedEl.onchange = async () => {
    $('speed-value').textContent = `${speedEl.value}×`;
    if (state.runId) {
      const snapshot = await request(`/api/runs/${state.runId}/speed`, {
        method: 'PATCH',
        body: JSON.stringify({ speed: Number(speedEl.value) }),
      });
      render(snapshot);
    }
  };
}

// === Bootstrap ===
async function init() {
  checkServer();
  
  const params = parseTradeVisualParams();
  if (!params) {
    document.getElementById('loading-stage').textContent = 'Missing required URL parameter: symbol';
    return;
  }
  
  state.tradeVisualParams = params;
  await initVisualMode(params);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

setInterval(checkServer, 5000);