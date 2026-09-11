'use strict';
/* HourBounce Review Lite: слева список архивных сигналов, справа один M5 график + табы T/SL */
const $ = (id) => document.getElementById(id);
const state = {
  signals: [], sel: null, entry: 'T1', sl: 3, mode: 'matrix', forceRefresh: false,
  execMode: 'grid',
  chart: null, series: null, markers: null, trailSeries: null,
  priceLines: [], mcharts: [], centerRange: null,
};

async function api(url) {
  const r = await fetch(url);
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || 'request failed');
  return d;
}

function badge(out) {
  const m = { TAKE: 'b-take', STOP: 'b-stop', NO_ENTRY: 'b-noentry', EXPIRED: 'b-expired' };
  return `<span class="badge ${m[out] || 'b-noentry'}">${out || '—'}</span>`;
}

// Биржевая точность цены символа (приходит в signal.price_precision/tick_size).
function precOf(signal) {
  const p = Number(signal && signal.price_precision);
  return Number.isFinite(p) ? Math.max(0, Math.min(8, p)) : 4;
}
function priceFormatOf(signal) {
  const fmt = { type: 'price', precision: precOf(signal) };
  const tick = Number(signal && signal.tick_size);
  if (Number.isFinite(tick) && tick > 0) fmt.minMove = tick;
  return fmt;
}
function fmtP(v, signal) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return n.toFixed(precOf(signal));
}
function shortDt(iso) {
  if (!iso) return '—';
  return String(iso).replace('T', ' ').replace(/\+00:00|Z$/, ' UTC').slice(0, 22);
}
// Центрирование цены: уровень ровно посередине шкалы при открытии.
// Считаем симметричный относительно уровня диапазон; сама установка —
// через autoscaleInfoProvider СВЕЖЕЙ серии (переиспользованная серия тащит
// старый масштаб при смене инструмента — поэтому серию пересоздаём).
function calcCenterRange(level, candles, extraPrices) {
  const lv = Number(level);
  if (!Number.isFinite(lv) || !candles.length) return null;
  let mn = Infinity, mx = -Infinity;
  candles.forEach((c) => { mn = Math.min(mn, c.low); mx = Math.max(mx, c.high); });
  (extraPrices || []).forEach((v) => {
    if (v === null || v === undefined || v === '') return; // Number(null)===0 — иначе шкала схлопнется к нулю
    const n = Number(v);
    if (Number.isFinite(n)) { mn = Math.min(mn, n); mx = Math.max(mx, n); }
  });
  if (!Number.isFinite(mn) || !Number.isFinite(mx) || mn >= mx) return null;
  const half = Math.max(Math.abs(mx - lv), Math.abs(lv - mn), Math.abs(lv) * 0.0005 || 0.000001) * 1.15;
  return { minValue: lv - half, maxValue: lv + half };
}
// Ось времени строго в UTC: LWC по умолчанию рисует подписи в поясе браузера.
function utcTick(t, type) {
  const d = new Date(t * 1000);
  const p = (n) => String(n).padStart(2, '0');
  const hm = `${p(d.getUTCHours())}:${p(d.getUTCMinutes())} UTC`;
  if (type === 'time') return hm;
  return `${p(d.getUTCDate())}.${p(d.getUTCMonth() + 1)} ${hm}`;
}

async function loadSignals() {
  const q = new URLSearchParams({
    search: $('f-search').value.trim(),
    side: $('f-side').value,
    limit: '200',
    sort: $('f-sort').value,
  });
  if ($('f-from').value) q.set('date_from', $('f-from').value);
  if ($('f-to').value) q.set('date_to', $('f-to').value);
  const d = await api('/api/hourbounce/signals?' + q.toString());
  let items = d.items || [];
  const fo = $('f-outcome').value;
  if (fo) items = items.filter((s) => s.outcome_arch === fo);
  state.signals = items;
  $('sig-count').textContent = `сигналов: ${items.length} (показаны первые 200)`;
  const box = $('siglist');
  box.innerHTML = '';
  items.forEach((s, i) => {
    const div = document.createElement('div');
    div.className = 'sigrow' + (state.sel && state.sel.signal_id === s.signal_id ? ' sel' : '');
    div.innerHTML = `<div><div class="nm">${s.symbol} · ${s.side} · ${s.signal_id}</div>
      <div class="mt">lvl ${s.level_price} · выставлен ${s.dt_place || ''} UTC<br>Vol — · NATR — · TP ${s.tp_arch || '—'} · SL ${s.sl_arch || '—'}<br>touch PGv2 ${s.touch_ref ? s.touch_ref.replace('T', ' ').replace('Z', ' UTC') : '—'}</div></div>
      <div>${badge(s.outcome_arch)}</div>`;
    div.onclick = () => selectSignal(i);
    box.appendChild(div);
  });
  if (items.length && !state.sel) selectSignal(0);
}

function selectSignal(i) {
  state.sel = state.signals[i];
  document.querySelectorAll('.sigrow').forEach((el, k) => el.classList.toggle('sel', k === i));
  if (state.execMode === 'signal' && !state.sel.pg_available) {
    state.execMode = 'grid';
    setExecTabs();
  }
  if (state.mode === 'matrix') loadMatrix();
  else loadReview();
}

function setTabs() {
  document.querySelectorAll('#tabs-entry button').forEach((b) => {
    b.classList.toggle('on', b.dataset.e === state.entry);
    b.onclick = () => { state.entry = b.dataset.e; setTabs(); loadReview(); };
  });
  document.querySelectorAll('#tabs-sl button').forEach((b) => {
    b.classList.toggle('on', Number(b.dataset.s) === state.sl);
    b.onclick = () => { state.sl = Number(b.dataset.s); setTabs(); loadReview(); };
  });
}

function ensureChart() {
  if (state.chart) return;
  state.chart = LightweightCharts.createChart($('chart'), {
    autoSize: true,
    layout: { background: { color: 'transparent' }, textColor: '#8b96a5', fontSize: 11 },
    grid: { vertLines: { color: '#1e2630' }, horzLines: { color: '#1e2630' } },
    rightPriceScale: { borderColor: '#2a323d' },
    timeScale: { borderColor: '#2a323d', timeVisible: true, secondsVisible: false, rightOffset: 4, tickMarkFormatter: utcTick },
  });
  // Серия создаётся заново при каждом рендере (resetMainSeries) — см. ниже.
  state.series = null;
  state.markers = null;
}

// Свежая серия под каждый сигнал: иначе ценовая шкала наследует масштаб
// прошлого инструмента и до новой цены приходится долго листать.
function resetMainSeries(signal) {
  if (state.series) { try { state.chart.removeSeries(state.series); } catch (_) {} }
  if (state.trailSeries) { try { state.chart.removeSeries(state.trailSeries); } catch (_) {} state.trailSeries = null; }
  state.series = state.chart.addSeries(LightweightCharts.CandlestickSeries, {
    upColor: '#26a69a', downColor: '#ef5350', borderVisible: false,
    wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    priceFormat: priceFormatOf(signal),
    autoscaleInfoProvider: (base) => (state.centerRange ? { priceRange: state.centerRange } : base()),
  });
  state.markers = LightweightCharts.createSeriesMarkers(state.series, []);
  state.priceLines = [];
}

function setExecTabs() {
  const pgOk = !state.sel || state.sel.pg_available !== false;
  document.querySelectorAll('#tabs-exec button').forEach((b) => {
    const isSig = b.dataset.x === 'signal';
    b.classList.toggle('on', (isSig ? 'signal' : 'grid') === state.execMode);
    b.disabled = isSig && !pgOk;
    b.style.opacity = b.disabled ? '.4' : '';
    b.title = isSig && !pgOk ? 'В архиве нет stop_loss — режим недоступен' : '';
    b.onclick = () => {
      if (b.disabled) return;
      state.execMode = b.dataset.x;
      setExecTabs();
      loadReview();
    };
  });
  document.querySelectorAll('#tabs-sl button').forEach((b) => {
    const dis = state.execMode === 'signal';
    b.disabled = dis;
    b.style.opacity = dis ? '.4' : '';
  });
}

async function loadReview() {
  if (!state.sel) return;
  const s = state.sel;
  $('res-line').textContent = `${s.symbol} · ${s.side} · level ${s.level_price} · ${s.dt_place || ''} — загрузка...`;
  $('banner').classList.remove('on');
  const rf = state.forceRefresh ? '&refresh=1' : '';
  state.forceRefresh = false;
  let d;
  try {
    d = await api(`/api/hourbounce/review?signal_id=${encodeURIComponent(s.signal_id)}&entry=${state.entry}&sl=${state.sl}&mode=${state.execMode}${rf}`);
  } catch (e) {
    $('res-line').textContent = 'ошибка: ' + e.message;
    return;
  }
  renderReview(d);
}

function renderReview(d) {
  ensureChart();
  const { signal, params, result, candles } = d;
  state.centerRange = calcCenterRange(signal.level_price, candles,
    [result.sl_price, result.be_price, result.entry_price, result.exit_price]
      .concat((result.trail_path || []).map((p) => p.value)));
  resetMainSeries(signal);
  try {
    state.series.setData(candles);

    const lvl = Number(signal.level_price);
    const isSig = d.params && d.params.mode === 'signal';
    state.priceLines.push(state.series.createPriceLine({ price: lvl, color: '#ff9f43', lineWidth: 2, lineStyle: 2, axisLabelVisible: true, title: 'H1' }));
    if (result.sl_price) {
      state.priceLines.push(state.series.createPriceLine({ price: Number(result.sl_price), color: '#ef5350', lineWidth: 1, lineStyle: 0, axisLabelVisible: true, title: isSig ? 'SL PG' : `SL ${params.sl_pct}%` }));
    }
    // перенос стопа в БУ: пунктирная линия + маркер (PGv2 1:1)
    const beEv = (result.events || []).find((e) => e.type === 'breakeven' && e.price);
    if (beEv) {
      state.priceLines.push(state.series.createPriceLine({ price: Number(beEv.price), color: '#f7c948', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: 'БУ' }));
    }
    if (result.trail_path && result.trail_path.length) {
      state.trailSeries = state.chart.addSeries(LightweightCharts.LineSeries, { color: '#26a69a', lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      state.trailSeries.setData(result.trail_path.filter((p) => p.time).map((p) => ({ time: p.time, value: p.value })));
    }

    const tmap = {};
    candles.forEach((c) => { tmap[c.time] = true; });
    const at = (iso) => { if (!iso) return null; const t = Math.floor(new Date(iso).getTime() / 1000); return tmap[t] ? t : nearestTime(t); };
    const times = candles.map((c) => c.time);
    function nearestTime(t) { let best = times[0], bd = 1e18; for (const x of times) { const dd = Math.abs(x - t); if (dd < bd) { bd = dd; best = x; } } return best; }

  const mk = [];
  // выставление уровня — виртуальная история: метка, только если свеча в окне
  if (signal.place_ts && tmap[signal.place_ts]) {
    mk.push({ time: signal.place_ts, position: 'belowBar', color: '#8b96a5', shape: 'square', text: 'выставлен' });
  }
  if (beEv && at(beEv.dt)) mk.push({ time: at(beEv.dt), position: 'aboveBar', color: '#f7c948', shape: 'circle', text: 'БУ' });
  if (result.touch_dt) mk.push({ time: at(result.touch_dt), position: 'belowBar', color: '#ff9f43', shape: 'circle', text: '① touch' });
    (result.confirm_dt || []).forEach((dt, k) => mk.push({ time: at(dt), position: 'aboveBar', color: '#2962ff', shape: 'square', text: `② C${k + 1}` }));
    if (result.entry_dt) mk.push({ time: at(result.entry_dt), position: 'belowBar', color: '#d7dee8', shape: 'arrowUp', text: `③ ${fmtP(result.entry_price, signal)}` });
    if ((result.trail_path || []).length && result.trail_path[0].time) mk.push({ time: result.trail_path[0].time, position: 'aboveBar', color: '#26a69a', shape: 'circle', text: '④ trail on' });
  if (result.exit_dt) {
    const col = result.outcome === 'TAKE' ? '#26a69a' : result.outcome === 'STOP' ? '#ef5350' : result.outcome === 'EXPIRED' ? '#f7c948' : '#6b7280';
    mk.push({ time: at(result.exit_dt), position: 'aboveBar', color: col, shape: result.outcome === 'TAKE' ? 'arrowDown' : 'square', text: `${result.outcome} ${fmtP(result.exit_price, signal)}` });
  }
    state.markers.setMarkers(mk.filter((m) => m.time));
  } finally {
    state.chart.timeScale().fitContent();
  }

  $('res-badge').className = 'badge ' + ({ TAKE: 'b-take', STOP: 'b-stop', NO_ENTRY: 'b-noentry', EXPIRED: 'b-expired' }[result.outcome] || 'b-noentry');
  $('res-badge').textContent = result.outcome + (result.ambiguous ? ' ~' : '') + (result.reason ? ` (${result.reason})` : '');
  $('res-line').textContent = `${signal.symbol} · ${signal.side} · ${state.entry}/SL${params.sl_index} · вход ${fmtP(result.entry_price, signal)} · выход ${fmtP(result.exit_price, signal)} · touch наш ${shortDt(result.touch_dt)} · PGv2 ${shortDt(signal.touch_ref)}${d.cache && d.cache.cells_hit ? ' · из кеша' : ''}`;
  const bn = $('banner');
  if (result.mode === 'placement') {
    bn.innerHTML = `С момента выставления уровня (<b>${signal.dt_place} UTC</b>) касания не было — ситуация не отработала во всей загруженной истории. ` +
      `Touch PGv2: <b>${shortDt(signal.touch_ref)}</b>.`;
    bn.classList.add('on');
  } else {
    bn.classList.remove('on');
  }
  $('head-sub').textContent = `сигнал ${signal.symbol} ${signal.signal_id} · level ${signal.level_price} · ${signal.side} · арх. ${state.sel.outcome_arch}`;

  const hasC = state.entry === 'T1' ? null : (result.confirm_dt || []).length > 0;
  const s4 = result.outcome === 'TAKE' ? 'ok-take' : result.outcome === 'STOP' ? 'ok-stop' : 'on';
  const s4label = result.outcome === 'TAKE' ? (result.exit_kind === 'tp' ? 'TP' : 'trail') : result.outcome === 'STOP' ? 'SL' : result.outcome;
  $('stepper').innerHTML = `
    <div class="step on"><div class="dot">1</div>touch ${result.touch_dt ? '✓' : '—'}</div>
    <div class="step ${hasC === null ? 'na' : hasC ? 'on' : ''}"><div class="dot">2</div>${hasC === null ? 'N/A' : 'confirm'}</div>
    <div class="step ${result.entry_dt ? 'on' : 'na'}"><div class="dot">3</div>entry</div>
    <div class="step ${s4}"><div class="dot">4</div>${s4label}</div>`;
  const ex = d.exec;
  $('exec-line').textContent = ex ? `PG: SL ${ex.sl} · TP ${ex.tp || '—'} · БУ ${ex.be || '—'} · trail ${ex.trail || '—'}${ex.trail_tp_only ? ' (только trail)' : ''}` : '';
  $('metrics').innerHTML = `<span>R <b>${result.r_multiple || '—'}</b></span><span>max+ <b>${result.max_profit_pct || '—'}%</b></span><span>MAE <b>${result.mae_pct || '—'}%</b></span><span>MFE <b>${result.mfe_pct || '—'}%</b></span><span>баров <b>${result.bars_in_trade}</b></span><span>trail точек <b>${(result.trail_path || []).length}</b></span>`;
  $('evbody').innerHTML = (result.events || []).map((e) => `<tr><td>${e.seq}</td><td>${e.type}</td><td>${shortDt(e.dt)}</td><td>${e.price || '—'}</td></tr>`).join('');
}

$('f-load').onclick = loadSignals;
$('f-search').addEventListener('keydown', (e) => { if (e.key === 'Enter') loadSignals(); });
document.addEventListener('keydown', (e) => {
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
  if (!state.signals.length) return;
  e.preventDefault();
  let i = state.signals.findIndex((s) => state.sel && s.signal_id === state.sel.signal_id);
  i = i < 0 ? 0 : (i + (e.key === 'ArrowDown' ? 1 : -1) + state.signals.length) % state.signals.length;
  selectSignal(i);
  document.querySelectorAll('.sigrow')[i]?.scrollIntoView({ block: 'nearest' });
});

/* ---------- matrix 3x3 ---------- */
function setMode(m) {
  state.mode = m;
  $('m-single').classList.toggle('on', m === 'single');
  $('m-matrix').classList.toggle('on', m === 'matrix');
  $('matrix').classList.toggle('on', m === 'matrix');
  $('agg').classList.toggle('on', m === 'matrix');
  const single = m === 'single';
  $('chart').style.display = single ? '' : 'none';
  $('stepper').style.display = single ? '' : 'none';
  $('metrics').style.display = single ? '' : 'none';
  $('tabs-entry').style.display = single ? '' : 'none';
  $('tabs-sl').style.display = single ? '' : 'none';
  $('tabs-exec').style.display = single ? '' : 'none';
  $('exec-line').style.display = single ? '' : 'none';
  if (!state.sel) return;
  if (m === 'matrix') loadMatrix();
  else loadReview();
}
$('m-single').onclick = () => setMode('single');
$('m-matrix').onclick = () => setMode('matrix');
$('m-export').onclick = () => {
  if (!state.sel) return;
  window.open('/api/hourbounce/export?signal_id=' + encodeURIComponent(state.sel.signal_id), '_blank');
};
$('m-refresh').onclick = () => {
  if (!state.sel) return;
  state.forceRefresh = true;
  if (state.mode === 'matrix') loadMatrix();
  else loadReview();
};

function destroyMatrix() {
  state.mcharts.forEach(({ chart }) => { try { chart.remove(); } catch (_) {} });
  state.mcharts = [];
  $('matrix').innerHTML = '';
}

function cellMarkers(cell, tmap, times) {
  const at = (iso) => {
    if (!iso) return null;
    const t = Math.floor(new Date(iso).getTime() / 1000);
    if (tmap[t]) return t;
    let best = times[0], bd = 1e18;
    for (const x of times) { const dd = Math.abs(x - t); if (dd < bd) { bd = dd; best = x; } }
    return best;
  };
  const mk = [];
  if (cell.touch_dt) mk.push({ time: at(cell.touch_dt), position: 'belowBar', color: '#ff9f43', shape: 'circle', text: '①' });
  (cell.confirm_dt || []).forEach(() => {});
  if (cell.entry_dt) mk.push({ time: at(cell.entry_dt), position: 'belowBar', color: '#d7dee8', shape: 'arrowUp', text: '③' });
  if (cell.exit_dt) {
    const col = cell.outcome === 'TAKE' ? '#26a69a' : cell.outcome === 'STOP' ? '#ef5350' : '#6b7280';
    mk.push({ time: at(cell.exit_dt), position: 'aboveBar', color: col, shape: 'square', text: cell.outcome });
  }
  return mk.filter((m) => m.time);
}

async function loadMatrix() {
  if (!state.sel) return;
  const s = state.sel;
  $('res-line').textContent = `${s.symbol} · матрица 9 комбинаций — загрузка...`;
  $('banner').classList.remove('on');
  const rf = state.forceRefresh ? '&refresh=1' : '';
  state.forceRefresh = false;
  let d;
  try {
    d = await api(`/api/hourbounce/matrix?signal_id=${encodeURIComponent(s.signal_id)}${rf}`);
  } catch (e) {
    $('res-line').textContent = 'ошибка: ' + e.message;
    return;
  }
  destroyMatrix();
  const box = $('matrix');
  const counts = { TAKE: 0, STOP: 0, NO_ENTRY: 0, EXPIRED: 0 };
  d.cells.forEach((c) => { counts[c.outcome] = (counts[c.outcome] || 0) + 1; });
  $('agg').innerHTML = `<span>winrate <b>${Math.round((counts.TAKE / 9) * 100)}%</b></span>
    <span>TAKE <b>${counts.TAKE}</b></span><span>STOP <b>${counts.STOP}</b></span>
    <span>NO_ENTRY <b>${counts.NO_ENTRY}</b></span><span>EXPIRED <b>${counts.EXPIRED}</b></span>
    <span>арх. исход <b>${s.outcome_arch}</b></span>
    <span>${d.cache && d.cache.cells_hit ? 'из кеша' : 'посчитано'} · свечей из кеша <b>${(d.cache && d.cache.candles_cached) ?? '—'}</b></span>
    <span>окно: от выставления до ближайшей отработки · всё время UTC</span>`;
  $('res-badge').textContent = `${counts.TAKE}T / ${counts.STOP}S / ${counts.NO_ENTRY}NE`;
  $('res-line').textContent = `${s.symbol} · ${s.side} · level ${s.level_price} · M5 · 9 комбинаций · touch PGv2 ${shortDt(d.signal.touch_ref)}`;

  box.appendChild(document.createElement('div'));
  const slSizes = d.sl_sizes || ['0.5', '1.0', '1.5'];
  [1, 2, 3].forEach((sl) => {
    const el = document.createElement('div');
    el.className = 'colh';
    el.textContent = `SL${sl} · ${slSizes[sl - 1]}%`;
    box.appendChild(el);
  });
  const labels = { T1: 'T1 · касание', T2: 'T2 · 1 бар', T3: 'T3 · 2 бара' };
  let syncing = false;
  ['T1', 'T2', 'T3'].forEach((code) => {
    const rh = document.createElement('div');
    rh.className = 'rowh';
    rh.textContent = labels[code];
    box.appendChild(rh);
    [1, 2, 3].forEach((sl) => {
      const cell = d.cells.find((c) => c.entry === code && c.sl_index === sl);
      const card = document.createElement('div');
      card.className = 'mcard';
      const cls = { TAKE: 'b-take', STOP: 'b-stop', NO_ENTRY: 'b-noentry', EXPIRED: 'b-expired' }[cell.outcome];
      card.innerHTML = `<div class="mhead"><span>T${code[1]}·SL${sl} · M5</span><span class="badge ${cls}">${cell.outcome}</span></div><div class="mchart"></div><div class="mfoot">in ${cell.entry_price || '—'} · out ${cell.exit_price || '—'} · R ${cell.r_multiple || '—'}</div>`;
      box.appendChild(card);
      const chartDiv = card.querySelector('.mchart');
      const chart = LightweightCharts.createChart(chartDiv, {
        autoSize: true,
        layout: { background: { color: 'transparent' }, textColor: '#8b96a5', fontSize: 10 },
        grid: { vertLines: { color: '#1e2630' }, horzLines: { color: '#1e2630' } },
        rightPriceScale: { borderColor: '#2a323d' },
        timeScale: { borderColor: '#2a323d', timeVisible: true, rightOffset: 3, tickMarkFormatter: utcTick },
      });
      const slice = d.candles.slice(cell.lo, cell.hi);
      const range = calcCenterRange(d.signal.level_price, slice,
        [cell.sl_price, cell.entry_price, cell.exit_price]
          .concat(((cell.trail_path || []).filter((p) => p.time)).map((p) => p.value)));
      const series = chart.addSeries(LightweightCharts.CandlestickSeries, {
        upColor: '#26a69a', downColor: '#ef5350', borderVisible: false,
        wickUpColor: '#26a69a', wickDownColor: '#ef5350',
        priceFormat: priceFormatOf(d.signal),
        autoscaleInfoProvider: (base) => (range ? { priceRange: range } : base()),
      });
      series.setData(slice);
      series.createPriceLine({ price: Number(d.signal.level_price), color: '#ff9f43', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '' });
      if (cell.sl_price) series.createPriceLine({ price: Number(cell.sl_price), color: '#ef5350', lineWidth: 1, axisLabelVisible: true, title: '' });
      const tp = (cell.trail_path || []).filter((p) => p.time);
      if (tp.length) {
        const ts = chart.addSeries(LightweightCharts.LineSeries, { color: '#26a69a', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
        ts.setData(tp);
      }
      const tmap = {};
      slice.forEach((c) => { tmap[c.time] = 1; });
      const mk = cellMarkers(cell, tmap, slice.map((c) => c.time));
      LightweightCharts.createSeriesMarkers(series, mk);
      chart.timeScale().fitContent();
      state.mcharts.push({ chart, series });
      // sync ranges
      chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (!$('m-sync').checked || syncing || !range) return;
        syncing = true;
        try {
          state.mcharts.forEach(({ chart: ch }) => { if (ch !== chart) ch.timeScale().setVisibleLogicalRange(range); });
        } catch (_) {}
        syncing = false;
      });
    });
  });
}

setTabs();
setExecTabs();
setMode('matrix');
loadSignals();
fetch('/api/version').then((r) => r.json()).then((d) => {
  if (d && d.version) $('app-version').textContent = 'v' + d.version;
}).catch(() => {});
