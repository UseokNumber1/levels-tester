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
  detailPrevMs: null,
  detailAnimTimer: null,
};
const $ = (id) => document.getElementById(id);

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

function render(snapshot) {
  state.runId = snapshot.run_id;
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

  if (!isLoading && snapshot.master_candles.length > 0) {
    state.allCandles = snapshot.master_candles;
    syncChartToCursor(snapshot);
  }

  if (wasLoading && !isLoading && snapshot.status === 'ready') {
    startAutoPlay();
  }
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
    if (state.candleSeries.setMarkers) state.candleSeries.setMarkers([]);
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

  if (state.drawnTime === undefined || lastTime === null) {
    state.candleSeries.setData(bars);
    state.chart.timeScale().scrollToPosition(5, false);
  } else {
    for (const bar of bars) {
      if (bar.time > state.drawnTime) {
        state.candleSeries.update(bar);
      }
    }
  }

  state.chartHasData = bars.length > 0;
  if (lastTime !== null) {
    state.drawnTime = lastTime;
  }

  renderLevelLines(snapshot.levels.filter(isConfirmedLevel));

  if (state.candleSeries.setMarkers) {
    state.candleSeries.setMarkers(snapshot.pivots.map(pivot => ({
      time: Math.floor(new Date(pivot.pivot_time).getTime() / 1000),
      position: pivot.kind === 'high' ? 'aboveBar' : 'belowBar',
      color: pivot.kind === 'high' ? '#ee6c4d' : '#2166f3',
      shape: pivot.kind === 'high' ? 'arrowDown' : 'arrowUp',
      text: pivot.kind,
    })));
  }
}

function clearLevelLines() {
  state.levelLines.forEach(line => state.candleSeries.removePriceLine(line));
  state.levelLines = [];
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

async function autoPlayStep() {
  if (!state.animRunning || !state.runId) return;
  try {
    const snapshot = await request(`/api/runs/${state.runId}/step`, { method: 'POST' });
    render(snapshot);
    const beforeDisplay = snapshot.cursor && state.displayFromMs
      && new Date(snapshot.cursor.bar_time).getTime() < state.displayFromMs;
    if (!beforeDisplay) {
      const touchedOnCurrentBar = snapshot.events.some(event =>
        event.event_type === 'level.touched' &&
        snapshot.cursor && event.sequence === snapshot.cursor.sequence
      );
      if (touchedOnCurrentBar) {
        await syncDetail(snapshot);
        stopAnimation();
        showHint('⏸ Пауза — уровень затронут, нажмите Play или Step для продолжения');
        return;
      }
      await syncDetail(snapshot);
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
    render(snapshot);
    if (name === 'step' && snapshot.cursor) {
      await syncDetail(snapshot);
    }
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
        detail_timeframe: '1m',
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
    layout: { background: { color: '#fffdf8' }, textColor: '#71808a' },
    grid: { vertLines: { color: '#eeeae1' }, horzLines: { color: '#eeeae1' } },
    timeScale: { timeVisible: true, rightOffset: 5, barSpacing: 6 },
  });
  state.candleSeries = state.chart.addCandlestickSeries({
    upColor: '#198754', downColor: '#ee6c4d',
    borderVisible: false,
    wickUpColor: '#198754', wickDownColor: '#ee6c4d',
  });
  if (window.ResizeObserver) {
    const resizeObserver = new ResizeObserver(() => {
      state.chart.resize($('chart').clientWidth, $('chart').clientHeight);
    });
    resizeObserver.observe($('chart'));
  }
}

const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

function initDetailChart() {
  if (state.detailChart) return;
  const container = $('detail-chart');
  if (!window.LightweightCharts || !container) return;
  state.detailChart = LightweightCharts.createChart(container, {
    layout: { background: { color: '#fffdf8' }, textColor: '#71808a' },
    grid: { vertLines: { color: '#eeeae1' }, horzLines: { color: '#eeeae1' } },
    timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 2 },
  });
  state.detailSeries = state.detailChart.addCandlestickSeries({
    upColor: '#198754', downColor: '#ee6c4d',
    borderVisible: false,
    wickUpColor: '#198754', wickDownColor: '#ee6c4d',
  });
  if (window.ResizeObserver) {
    const resizeObserver = new ResizeObserver(() => {
      state.detailChart.resize(container.clientWidth, container.clientHeight);
    });
    resizeObserver.observe(container);
  }
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

function resetDetailChart() {
  stopDetailAnimation();
  state.detailActive = false;
  state.detailPrevMs = null;
  if (state.detailSeries) state.detailSeries.setData([]);
  const container = $('detail-chart');
  container.classList.add('detail-waiting');
  container.textContent = 'Chart opens on level touch.';
  $('detail-status').textContent = 'waiting for touch';
}

function stopDetailAnimation() {
  if (state.detailAnimTimer) {
    clearTimeout(state.detailAnimTimer);
    state.detailAnimTimer = null;
  }
}

async function syncDetail(snapshot) {
  if (!snapshot.cursor) return;
  const cursorMs = new Date(snapshot.cursor.bar_time).getTime();
  const touchedOnCurrentBar = snapshot.events.some(event =>
    event.event_type === 'level.touched' &&
    snapshot.cursor && event.sequence === snapshot.cursor.sequence
  );
  if (!state.detailActive && !touchedOnCurrentBar) return;
  openDetailPanel();
  const startMs = state.detailPrevMs ?? cursorMs - 3600 * 1000;
  state.detailPrevMs = cursorMs;
  if (cursorMs <= startMs) return;
  try {
    const params = new URLSearchParams({
      start: new Date(startMs).toISOString(),
      end: new Date(cursorMs).toISOString(),
    });
    const data = await request(`/api/runs/${state.runId}/detail?${params}`);
    await animateDetailCandles(data.detail_candles || [], startMs, cursorMs);
  } catch (_) {
    $('detail-status').textContent = 'detail unavailable';
  }
}

async function animateDetailCandles(candles, startMs, endMs) {
  stopDetailAnimation();
  initDetailChart();
  if (!state.detailSeries) return;
  const bars = candles
    .filter(c => {
      const t = new Date(c.open_time).getTime();
      return t > startMs && t <= endMs;
    })
    .map(c => ({
      time: Math.floor(new Date(c.open_time).getTime() / 1000),
      open: Number(c.open), high: Number(c.high),
      low: Number(c.low), close: Number(c.close),
    }));
  $('detail-status').textContent = `${bars.length} M1 · ${utcFormat(endMs)} UTC`;
  if (!bars.length) return;
  for (const bar of bars) {
    if (!state.detailActive) return;
    state.detailSeries.update(bar);
    await sleep(Math.max(30, Math.round(1000 / (Number($('speed').value) || 1))));
  }
}

checkServer();
loadInstruments(false);
setInterval(checkServer, 5000);
