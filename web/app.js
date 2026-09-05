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
  detailSetupMarkers: null,
  activeSetupId: null,
  pivotMarkers: null,
  pricePrecision: 2,
  tickSize: '0.01',
};
const $ = (id) => document.getElementById(id);
const RIGHT_OFFSET_BARS = 5;

function selectedConfirmationMethods() {
  const checked = document.querySelector('input[name="confirmation-method"]:checked');
  return checked ? [checked.value] : [];
}

function stepDelay() {
  const speed = Number($('speed').value) || 1;
  return Math.max(8, Math.round(1000 / speed));
}

function scrollChartToRight(chart) {  if (!chart) return;
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
const displayFromEl = $('display-from');
if (displayFromEl) displayFromEl.value = isoInput(new Date(Date.now() - 7 * 86400000));

function formatVolume(vol) {
  const n = Number(vol);
  if (n >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `$${(n / 1e3).toFixed(0)}K`;
  return `$${n.toLocaleString()}`;
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

async function checkServer() {
  try {
    const status = await request('/api/status');
    const connected = status.backend === 'connected' && status.database === 'connected' && status.database_schema === 'valid';
    $('server-dot').className = `dot ${connected ? 'ok' : 'bad'}`;
    $('server-status').textContent = `SERVER ${connected ? 'CONNECTED' : 'DEGRADED'} · SQL ${status.database_timezone} · BINANCE ${status.binance.toUpperCase()}`;
  } catch (_) { $('server-dot').className = 'dot bad'; $('server-status').textContent = 'SERVER DISCONNECTED'; }
}

async function loadInstruments(force = false) {
  const searchEl = $('instrument-search');
  if (!searchEl) return;
  const params = new URLSearchParams({ search: searchEl.value, limit: '100', refresh: String(force) });
  const minVolEl = $('min-volume');
  if (minVolEl && minVolEl.value) params.set('min_volume', minVolEl.value);
  try {
    const data = await request(`/api/instruments?${params}`);
    const symbolEl = $('symbol');
    const infoEl = $('instrument-info');
    if (symbolEl) symbolEl.innerHTML = data.items.length
      ? data.items.map(item => `<option value="${item.symbol}">${item.symbol} · ${formatVolume(item.daily_volume_usdt)}</option>`).join('')
      : '<option>No instruments</option>';
    if (infoEl) infoEl.textContent = `${data.count} USDT instruments`;
    applyPendingTradeSymbol();
  } catch (error) { const infoEl = $('instrument-info'); if (infoEl) infoEl.textContent = error.message; }
}

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

  const methodEl = document.getElementById('trade-method');
  if (methodEl && !methodEl.textContent) {
    const activeSetup = findActiveSetup(snapshot);
    if (activeSetup && activeSetup.confirmation_method) {
      const method = activeSetup.confirmation_method;
      const reqBars = activeSetup.confirmation_bars || 2;
      if (method === 'touch') {
        methodEl.textContent = 'Touch';
      } else if (method === 'consecutive') {
        methodEl.textContent = reqBars === 1 ? '1 bar' : '2 bars';
      } else {
        methodEl.textContent = method;
      }
    } else if (snapshot.confirmation_methods && snapshot.confirmation_methods.length) {
      methodEl.textContent = snapshot.confirmation_methods.join(', ');
    }
  }
  if (methodEl) console.log('[trade-method]', methodEl.textContent);

  if (!isLoading && snapshot.master_candles.length > 0 && !deferChart) {
    state.allCandles = snapshot.master_candles;
    syncChartToCursor(snapshot);
  }

  if (wasLoading && !isLoading && snapshot.status === 'ready') {
    // Auto-play starts on first manual Play click (not auto-start)
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

function findActiveSetup(snapshot) {
  const setups = snapshot.setups || [];
  if (state.activeSetupId) {
    const anchored = setups.find(setup => setup.id === state.activeSetupId);
    if (anchored) return anchored;
  }
  const cursorMs = snapshot.cursor ? new Date(snapshot.cursor.bar_time).getTime() : Date.now();
  const candidates = setups.filter(setup => new Date(setup.touch_time).getTime() <= cursorMs);
  if (!candidates.length) return null;
  candidates.sort(
    (a, b) => new Date(b.touch_time).getTime() - new Date(a.touch_time).getTime()
  );
  return candidates[0];
}

function barConfirms(bar, side, method, levelPrice) {
  const close = Number(bar.close);
  const open = Number(bar.open);
  const price = Number(levelPrice);
  if (side === 'support') {
    const inDirection = close > price;
    if (method === 'bounce') return close > open && inDirection;
    return inDirection;
  }
  const inDirection = close < price;
  if (method === 'bounce') return close < open && inDirection;
  return inDirection;
}

function drawSetupMarkers(setup, snapshot, bars) {
  if (!state.detailSetupMarkers) return;
  if (!setup) {
    state.detailSetupMarkers.setMarkers([]);
    return;
  }
  const touchMs = new Date(setup.touch_time).getTime();
  const touchBar = bars.find(bar => bar.time * 1000 >= touchMs) || bars[0];
  if (!touchBar) {
    state.detailSetupMarkers.setMarkers([]);
    return;
  }
  const side = setup.side;
  const method = setup.confirmation_method;
  const level = (snapshot.levels || []).find(item => item.id === setup.level_id);
  const levelPrice = level ? level.price : null;
  const requiredBars = Number($('confirmation-bars').value) || 2;
  const below = side === 'support' ? 'belowBar' : 'aboveBar';
  const above = side === 'support' ? 'aboveBar' : 'belowBar';
  const markers = [{ time: touchBar.time, position: below, color: '#888', shape: 'circle', text: 'T' }];

  if (method === 'touch') {
    const entryMs = new Date(setup.entry_time || setup.touch_time).getTime();
    const entryBar = bars.find(bar => bar.time * 1000 >= entryMs) || touchBar;
    markers.push({ time: entryBar.time, position: below, color: '#198754', shape: 'arrowUp', text: 'ENTRY' });
    state.detailSetupMarkers.setMarkers(markers);
    return;
  }

  let count = 0;
  for (const bar of bars) {
    if (bar.time * 1000 <= touchBar.time * 1000) continue;
    if (levelPrice == null) break;
    if (barConfirms(bar, side, method, levelPrice)) {
      count += 1;
      markers.push({ time: bar.time, position: below, color: '#198754', shape: 'circle', text: String(count) });
    } else {
      count = 0;
    }
  }

  if (setup.entry_time) {
    const entryMs = new Date(setup.entry_time).getTime();
    const entryBar = bars.find(bar => bar.time * 1000 >= entryMs) || touchBar;
    markers.push({ time: entryBar.time, position: below, color: '#198754', shape: 'arrowUp', text: 'ENTRY' });
  } else if (setup.cancelled_time || setup.status === 'cancelled' || setup.status === 'expired') {
    const cancelMs = new Date(setup.cancelled_time || setup.touch_time).getTime();
    const cancelBar = bars.find(bar => bar.time * 1000 >= cancelMs) || touchBar;
    markers.push({ time: cancelBar.time, position: above, color: '#ee6c4d', shape: 'arrowDown', text: 'CANCEL' });
  }
  state.detailSetupMarkers.setMarkers(markers);
}

function setupDetailStatus(setup, detailTf) {
  if (!setup) return '';
  const method = setup.confirmation_method;
  if (setup.entry_time) return `${detailTf} · ${method} ENTRY @ ${utcFormat(new Date(setup.entry_time).getTime())} UTC`;
  if (setup.cancelled_time || setup.status === 'cancelled' || setup.status === 'expired') {
    return `${detailTf} · ${method} CANCELLED (${setup.reason || ''})`;
  }
  const required = Number($('confirmation-bars').value) || 2;
  return `${detailTf} · ${method} confirm ${setup.confirmation_bars || 0}/${required} (waited ${setup.bars_waited || 0})`;
}

function showResolution(setup, snapshot) {
  const detailTf = $('detail-tf').value.toUpperCase();
  const method = setup.confirmation_method;
  const required = Number($('confirmation-bars').value) || 2;
  const entryTime = setup.entry_time
    ? utcFormat(new Date(setup.entry_time).getTime()) + ' UTC'
    : '';
  let msg;
  if (setup.entry_time) {
    if (method === 'touch') {
      msg = `Method 1 (touch): entry at ${entryTime}`;
    } else if (method === 'consecutive') {
      msg = `Method 3: ${required} consecutive closes beyond the level → ENTRY @ ${entryTime}`;
    } else {
      msg = `Method 5: ${required} directional candles past the level → ENTRY @ ${entryTime}`;
    }
    showHint(msg, true);
  } else {
    let detail;
    if (setup.reason && setup.reason.includes('timeout')) {
      detail = `exceeded max wait (${setup.bars_waited} bars)`;
    } else {
      detail = setup.reason || 'cancelled';
    }
    if (method === 'touch') {
      msg = `Method 1: setup cancelled (${detail})`;
    } else if (method === 'consecutive') {
      msg = `Method 3: not enough consecutive closes — ${detail}`;
    } else {
      msg = `Method 5: no ${required} directional candles past the level — ${detail}`;
    }
    showHint(msg, false);
  }
  $('detail-status').textContent = msg;
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
    if (state.detailEnabled) {
      await syncDetail(snapshot);
    }
    console.log('[autoPlayStep] seq:', snapshot.cursor ? snapshot.cursor.sequence : 'no cursor', 'time:', snapshot.cursor ? snapshot.cursor.bar_time : '-', 'events:', snapshot.events.length, 'status:', snapshot.status, 'levels:', (snapshot.levels||[]).length, 'setups:', (snapshot.setups||[]).length);
    snapshot.events.forEach(e => {
      console.log('  event:', e.event_type, 'seq:', e.sequence, 'level_id:', e.level_id||'-', 'reason:', e.reason||'-', 'payload:', JSON.stringify(e.payload||{}).slice(0,120));
    });
    // trace setups
    (snapshot.setups||[]).forEach(s => console.log('  setup:', s.id, 'method:', s.confirmation_method, 'status:', s.status, 'touch_time:', s.touch_time, 'entry_time:', s.entry_time||'-'));
    const currentSeq = snapshot.cursor ? snapshot.cursor.sequence : -1;
    const touchEvent = snapshot.events.find(event =>
      (event.event_type === 'level.touched' || event.event_type === 'level.broken' || event.event_type === 'level.approaching') &&
      event.sequence === currentSeq
    );
    if (touchEvent) {
      console.log('[autoPlayStep] LEVEL TOUCH/BROKEN/APPROACH found:', touchEvent.event_type, 'level_id:', touchEvent.level_id, 'reason:', touchEvent.reason);
      const level = (snapshot.levels || []).find(item => item.id === touchEvent.level_id);
      if (level) console.log('  level state:', level.state, 'side:', level.side, 'price:', level.price, 'touches:', level.touch_count);
      const active = level && !['expired'].includes(level.state);
      if (active) {
        const touchCandle = snapshot.master_candles.find(item => item.close_time === snapshot.cursor.bar_time);
        state.detailEnabled = true;
        state.detailPrevMs = touchCandle
          ? new Date(touchCandle.open_time).getTime()
          : new Date(snapshot.cursor.bar_time).getTime();
        console.log('[autoPlayStep] -> opening detail chart (level event) startMs:', new Date(state.detailPrevMs).toISOString());
        await syncDetail(snapshot);
        showHint('⏸ Пауза — активный уровень затронут, нажмите Play или Step для продолжения');
        stopAnimation();
        return;
      } else {
        console.log('[autoPlayStep] level NOT active, state:', level?.state);
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
    render(snapshot, name === 'step');
    if (name === 'step' && snapshot.cursor) {
      // Detail data is available from the very first step: the snapshot
      // exposes detail candles for the whole calculation window (warm-up
      // included), so there is no reason to gate it on display_from.
      await syncDetail(snapshot);
    }
    const resolvedSetup = (snapshot.setups || []).find(
      s => s.status === 'entry_confirmed' || s.status === 'cancelled' || s.status === 'expired'
    );
    if (resolvedSetup) showResolution(resolvedSetup, snapshot);
    drawH1Chart(snapshot);
    if (name === 'reset') resetDetailChart();
  } catch (error) { alert(error.message); }
}

const createBtn = $('create');
if (createBtn) createBtn.onclick = async () => {
  stopAnimation();
  state.lastStatus = null;
  state.drawnTime = undefined;
  state.chartHasData = false;
  state.totalBars = 0;
  state.displayFromMs = null;
  resetDetailChart();
  hideHint();
  try {
    const confirmationMethods = selectedConfirmationMethods();
    if (!confirmationMethods.length) {
      throw new Error('Select at least one confirmation method.');
    }
    const displayFromVal = $('display-from') ? $('display-from').value : '';
    const dateStr = displayFromVal + 'T00:00:00Z';
    state.displayFromMs = Date.parse(dateStr);
    const snapshot = await request('/api/runs', {
      method: 'POST',
      body: JSON.stringify({
        symbol: $('symbol').value,
        display_from: dateStr,
        detail_timeframe: selectedDetailTf(),
        confirmation_methods: confirmationMethods,
        confirmation_required_bars: Number($('confirmation-bars').value),
        confirmation_max_wait_bars: Number($('confirmation-max-wait').value),
      }),
    });
    render(snapshot);
    pollRun(snapshot.run_id);
  } catch (error) { alert(error.message); }
};

document.querySelectorAll('[data-command]').forEach(button => {
  button.onclick = () => command(button.dataset.command);
});

$('speed').oninput = () => {
  $('speed-value').textContent = `${$('speed').value}×`;
};
$('speed').onchange = async () => {
  $('speed-value').textContent = `${$('speed').value}×`;
  if (state.runId) {
    const snapshot = await request(`/api/runs/${state.runId}/speed`, {
      method: 'PATCH',
      body: JSON.stringify({ speed: Number($('speed').value) }),
    });
    render(snapshot);
  }
};

const refreshInstrumentsEl = $('refresh-instruments');
if (refreshInstrumentsEl) refreshInstrumentsEl.onclick = () => loadInstruments(true);
const instrumentSearchEl = $('instrument-search');
if (instrumentSearchEl) instrumentSearchEl.onchange = () => loadInstruments(false);
const minVolumeEl = $('min-volume');
if (minVolumeEl) minVolumeEl.onchange = () => loadInstruments(false);

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
    priceFormat: { type: 'price', precision: state.pricePrecision, minMove: Number(state.tickSize) },
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
    priceFormat: { type: 'price', precision: state.pricePrecision, minMove: Number(state.tickSize) },
  });
  state.detailSetupMarkers = LightweightCharts.createSeriesMarkers(state.detailSeries, []);
}

function openDetailPanel() {
  if (state.detailActive) { console.log('[detail] already active'); return; }
  state.detailActive = true;
  const container = $('detail-chart');
  container.classList.remove('detail-waiting');
  container.textContent = '';
  initDetailChart();
  $('detail-status').textContent = 'synced with H1';
  console.log('[detail] panel opened');
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
  state.activeSetupId = null;
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
  state.activeSetupId = null;
  clearDetailLevelLines();
  destroyDetailChart();
  $('detail-status').textContent = 'cleared — press Step for M1 replay';
}

async function syncDetail(snapshot) {
  if (!snapshot.cursor) return;
  const cursorMs = new Date(snapshot.cursor.bar_time).getTime();
  state.detailEnabled = true;
  openDetailPanel();
  let startMs = state.detailPrevMs ?? cursorMs - 3600 * 1000;
  if (startMs >= cursorMs) startMs = cursorMs - 3600 * 1000;
  const activeSetup = findActiveSetup(snapshot);
  if (activeSetup) state.activeSetupId = activeSetup.id;
  try {
    const params = new URLSearchParams({
      start: new Date(startMs).toISOString(),
      end: new Date(cursorMs).toISOString(),
    });
    const data = await request(`/api/runs/${state.runId}/detail?${params}`);
    state.detailPrevMs = cursorMs;
    await animateDetailCandles(data.detail_candles || [], startMs, cursorMs, activeSetup, snapshot);
  } catch (error) {
    console.error('detail load failed:', error);
    $('detail-status').textContent = `detail error: ${error && error.message ? error.message : 'unavailable'}`;
  }
}

async function animateDetailCandles(candles, startMs, endMs, activeSetup, snapshot) {
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
  if (!newBars.length) {
    drawSetupMarkers(activeSetup, snapshot, state.detailBars);
    return;
  }
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
  drawSetupMarkers(activeSetup, snapshot, state.detailBars);
  const status = setupDetailStatus(activeSetup, detailTf);
  if (status) $('detail-status').textContent = status;
}

checkServer();
loadReplayConfig();
loadInstruments(false);
setInterval(checkServer, 5000);
