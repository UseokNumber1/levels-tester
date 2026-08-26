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
  pivotMarkers: null,
};
const $ = (id) => document.getElementById(id);
const RIGHT_OFFSET_BARS = 5;

function scrollChartToRight(chart) {
  if (!chart) return;
  const timeScale = chart.timeScale();
  if (typeof timeScale.scrollToPosition === 'function') {
    // Preserve the current zoom and keep the newest bar at the right edge.
    timeScale.scrollToPosition(5, false);
  } else if (typeof timeScale.fitContent === 'function') {
    // Fallback: fit all data into view so scales and candles are visible.
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

const DETAIL_TF_KEY = 'levels-tester-detail-tf';

$('detail-tf').value = localStorage.getItem(DETAIL_TF_KEY) === '5m' ? '5m' : '1m';
updateDetailTitle();
$('detail-tf').onchange = () => {
  localStorage.setItem(DETAIL_TF_KEY, $('detail-tf').value);
  updateDetailTitle();
};

function updateDetailTitle() {
  $('detail-title').textContent = `Detail ${$('detail-tf').value === '5m' ? 'M5' : 'M1'}`;
}

function selectedDetailTf() {
  return $('detail-tf').value;
}

async function loadReplayConfig() {
  try {
    const config = await request('/api/config');
    const replay = config.replay || {};
    const defaultSpeed = Number(replay.default_speed);
    if (Number.isFinite(defaultSpeed) && defaultSpeed > 0) {
      $('speed').value = String(defaultSpeed);
    }
    const timeframes = Array.isArray(replay.detail_timeframes)
      ? replay.detail_timeframes.filter(item => ['1m', '5m'].includes(item))
      : [];
    if (timeframes.length) {
      $('detail-tf').innerHTML = timeframes
        .map(timeframe => `<option value="${timeframe}">${timeframe.toUpperCase()}</option>`)
        .join('');
      const saved = localStorage.getItem(DETAIL_TF_KEY);
      $('detail-tf').value = timeframes.includes(saved)
        ? saved
        : (replay.default_detail_timeframe || timeframes[0]);
      updateDetailTitle();
    }
  } catch (_) {
    // Keep the safe HTML defaults if the config endpoint is unavailable.
  }
}

function isoInput(value) {
  const d = new Date(value);
  return d.toISOString().slice(0, 10);
}
$('display-from').value = isoInput(new Date(Date.now() - 7 * 86400000));

function formatVolume(vol) {
  const n = Number(vol);
  if (n >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `$${(n / 1e3).toFixed(0)}K`;
  return `$${n.toLocaleString()}`;
}

function isConfirmedLevel(level) {
  return !['created', 'broken', 'expired'].includes(level.state);
}

function utcFormat(value) {
  const d = new Date(value);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

async function checkServer() {
  try {
    const status = await request('/api/status');
    const connected = status.backend === 'connected' && status.database === 'connected' && status.database_schema === 'valid';
    $('server-dot').className = `dot ${connected ? 'ok' : 'bad'}`;
    $('server-status').textContent = `SERVER ${connected ? 'CONNECTED' : 'DEGRADED'} · SQL ${status.database_timezone} · BINANCE ${status.binance.toUpperCase()}`;
  } catch (_) { $('server-dot').className = 'dot bad'; $('server-status').textContent = 'SERVER DISCONNECTED'; }
}

async function loadInstruments(force = false) {
  const params = new URLSearchParams({ search: $('instrument-search').value, limit: '100', refresh: String(force) });
  if ($('min-volume').value) params.set('min_volume', $('min-volume').value);
  try {
    const data = await request(`/api/instruments?${params}`);
    $('symbol').innerHTML = data.items.length
      ? data.items.map(item => `<option value="${item.symbol}">${item.symbol} · ${formatVolume(item.daily_volume_usdt)}</option>`).join('')
      : '<option>No instruments</option>';
    $('instrument-info').textContent = `${data.count} USDT instruments`;
  } catch (error) { $('instrument-info').textContent = error.message; }
}

function render(snapshot, deferChart = false) {
  state.runId = snapshot.run_id;
  if (Number.isFinite(Number(snapshot.speed))) {
    $('speed').value = String(snapshot.speed);
  }
  const isLoading = snapshot.status === 'loading';
  const wasLoading = state.lastStatus === 'loading';
  state.lastStatus = snapshot.status;

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
    <div class="level"><div class="level-title ${level.side}"><span>${level.side}</span><span>${level.price}</span></div>
    <div class="level-meta">${level.state} · ${level.touch_count} touches · zone ${level.zone_low}–${level.zone_high}</div></div>`).join('')
    : 'No levels confirmed yet.';

  if (!isLoading && snapshot.master_candles.length > 0 && !deferChart) {
    state.allCandles = snapshot.master_candles;
    syncChartToCursor(snapshot);
  }

  if (wasLoading && !isLoading && snapshot.status === 'ready') {
    startAutoPlay();
  }
}

function drawH1Chart(snapshot) {
  if (!snapshot || !snapshot.master_candles || snapshot.master_candles.length === 0) return;
  state.allCandles = snapshot.master_candles;
  syncChartToCursor(snapshot);
}

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
    // Initial load (or after Reset): paint all visible candles at once.
    // Load all bars visible at the current replay cursor.
    state.candleSeries.setData(bars);
    state.drawnTime = lastTime;
  } else {
    // Incremental step: add newer bars that come after what we already have.
    for (const bar of bars) {
      if (bar.time > state.drawnTime) {
        state.candleSeries.update(bar);
      }
    }
    // Remember the newest bar time for the next step.
    if (bars.length > 0) {
      state.drawnTime = bars[bars.length - 1].time;
    }
  }

  // Re-enable autoscaling after a symbol switch, e.g. BTC -> ZEC.
  enablePriceAutoScale(state.candleSeries);
  scrollChartToRight(state.chart);

  state.chartHasData = bars.length > 0;

  renderLevelLines(snapshot.levels.filter(isConfirmedLevel));
  renderDetailLevelLines(snapshot.levels.filter(isConfirmedLevel));

  setPivotMarkers(snapshot.pivots.map(pivot => ({
    time: Math.floor(new Date(pivot.pivot_time).getTime() / 1000),
    position: pivot.kind === 'high' ? 'aboveBar' : 'belowBar',
    color: pivot.kind === 'high' ? '#ee6c4d' : '#2166f3',
    shape: pivot.kind === 'high' ? 'arrowDown' : 'arrowUp',
    text: pivot.kind,
  })));
}

function clearLevelLines() {
  state.levelLines.forEach(line => state.candleSeries.removePriceLine(line));
  state.levelLines = [];
}

function clearDetailLevelLines() {
  if (!state.detailSeries) return;
  state.detailLevelLines.forEach(line => state.detailSeries.removePriceLine(line));
  state.detailLevelLines = [];
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
        title: `${level.side[0].toUpperCase()} ${level.price}`,
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

function renderLevelLines(levels) {
  clearLevelLines();
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

function startAutoPlay() {
  stopAnimation();
  state.animRunning = true;
  autoPlayStep();
}

function stopAnimation() {
  state.animRunning = false;
  if (state.animTimer) {
    clearTimeout(state.animTimer);
    state.animTimer = null;
  }
}

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

function priceRemainsInZone(snapshot, touchEvent) {
  const level = snapshot.levels.find(item => item.id === touchEvent.level_id);
  const candle = snapshot.master_candles.find(item =>
    item.close_time === snapshot.cursor.bar_time
  );
  if (!level || !candle) return false;
  const close = Number(candle.close);
  return close >= Number(level.zone_low) && close <= Number(level.zone_high);
}

async function autoPlayStep() {
  if (!state.animRunning || !state.runId) return;
  try {
    const snapshot = await request(`/api/runs/${state.runId}/step`, { method: 'POST' });
    render(snapshot);
    const beforeDisplay = snapshot.cursor && state.displayFromMs
      && new Date(snapshot.cursor.bar_time).getTime() < state.displayFromMs;
    if (!beforeDisplay) {
      const touchEvent = snapshot.events.find(event =>
        event.event_type === 'level.touched' &&
        snapshot.cursor && event.sequence === snapshot.cursor.sequence
      );
      if (touchEvent && priceRemainsInZone(snapshot, touchEvent)) {
        state.detailEnabled = true;
        openDetailPanel();
        $('detail-status').textContent = 'paused on touch — press Step for M1 replay';
        stopAnimation();
        showHint('⏸ Пауза — уровень затронут, нажмите Play или Step для продолжения');
        return;
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
    const delay = beforeDisplay ? 0 : Math.max(50, Math.round(1000 / (Number($('speed').value) || 1)));
    state.animTimer = setTimeout(autoPlayStep, delay);
  } catch (error) {
    stopAnimation();
  }
}

async function request(url, options = {}) {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || 'Request failed');
  return data;
}

async function command(name) {
  if (!state.runId) return;
  try {
    if (name === 'play') {
      hideHint();
      if (state.detailEnabled) clearDetailViewData();
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
      // Detail data is available from the very first step: the snapshot
      // exposes detail candles for the whole calculation window (warm-up
      // included), so there is no reason to gate it on display_from.
      await syncDetail(snapshot);
    }
    drawH1Chart(snapshot);
    if (name === 'reset') resetDetailChart();
  } catch (error) { alert(error.message); }
}

$('create').onclick = async () => {
  stopAnimation();
  state.lastStatus = null;
  state.drawnTime = undefined;
  state.chartHasData = false;
  state.totalBars = 0;
  state.displayFromMs = null;
  resetDetailChart();
  hideHint();
  try {
    const dateStr = $('display-from').value + 'T00:00:00Z';
    state.displayFromMs = Date.parse(dateStr);
    const snapshot = await request('/api/runs', {
      method: 'POST',
      body: JSON.stringify({
        symbol: $('symbol').value,
        display_from: dateStr,
        detail_timeframe: selectedDetailTf(),
      }),
    });
    render(snapshot);
    pollRun(snapshot.run_id);
  } catch (error) { alert(error.message); }
};

document.querySelectorAll('[data-command]').forEach(button => {
  button.onclick = () => command(button.dataset.command);
});

$('speed').onchange = async () => {
  if (state.runId) {
    const snapshot = await request(`/api/runs/${state.runId}/speed`, {
      method: 'PATCH',
      body: JSON.stringify({ speed: Number($('speed').value) }),
    });
    render(snapshot);
  }
};

$('refresh-instruments').onclick = () => loadInstruments(true);
$('instrument-search').onchange = () => loadInstruments(false);
$('min-volume').onchange = () => loadInstruments(false);

async function pollRun(runId) {
  for (;;) {
    await new Promise(resolve => setTimeout(resolve, 500));
    const snapshot = await request(`/api/runs/${runId}`);
    render(snapshot);
    if (snapshot.status !== 'loading') break;
  }
}

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
  });
}

function setPivotMarkers(markers) {
  if (!state.candleSeries) return;
  if (!state.pivotMarkers) {
    state.pivotMarkers = LightweightCharts.createSeriesMarkers(state.candleSeries, []);
  }
  state.pivotMarkers.setMarkers(markers);
}

const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

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
  });
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
  stopDetailAnimation();
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

function stopDetailAnimation() {
  state.detailAnimSeq++;
}

function clearDetailViewData() {
  stopDetailAnimation();
  state.detailPrevMs = null;
  state.detailDrawnTime = undefined;
  state.detailCount = 0;
  state.detailBars = [];
  clearDetailLevelLines();
  destroyDetailChart();
  $('detail-status').textContent = 'cleared — press Step for M1 replay';
}

async function syncDetail(snapshot) {
  if (!snapshot.cursor) return;
  const cursorMs = new Date(snapshot.cursor.bar_time).getTime();
  state.detailEnabled = true;
  openDetailPanel();
  const startMs = state.detailPrevMs ?? cursorMs - 3600 * 1000;
  if (cursorMs <= startMs) return;
  try {
    const params = new URLSearchParams({
      start: new Date(startMs).toISOString(),
      end: new Date(cursorMs).toISOString(),
    });
    const data = await request(`/api/runs/${state.runId}/detail?${params}`);
    // Advance the window before animating so an interrupted animation
    // never refetches or re-draws the same hour on the next Step.
    state.detailPrevMs = cursorMs;
    await animateDetailCandles(data.detail_candles || [], startMs, cursorMs);
  } catch (error) {
    console.error('detail load failed:', error);
    $('detail-status').textContent = `detail error: ${error && error.message ? error.message : 'unavailable'}`;
  }
}

async function animateDetailCandles(candles, startMs, endMs) {
  stopDetailAnimation();
  initDetailChart();
  if (!state.detailSeries) return;
  const animSeq = ++state.detailAnimSeq;
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

  // Draw every new detail candle separately, including on subsequent Step
  // presses. setData() is used with the accumulated data because it is
  // deterministic in the standalone v5 build; the chart viewport is adjusted
  // once after the whole batch has been drawn.
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
  // Adjust price scale and viewport once after the batch.
  enablePriceAutoScale(state.detailSeries);
  scrollChartToRight(state.detailChart);
}

checkServer();
loadReplayConfig();
loadInstruments(false);
setInterval(checkServer, 5000);
