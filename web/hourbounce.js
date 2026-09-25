'use strict';
/* HourBounce Review Lite: слева список архивных сигналов, справа один M5 график + табы T/SL */
const $ = (id) => document.getElementById(id);
const state = {
  signals: [], sel: null, entry: 'T1M', sl: 3, mode: 'matrix', forceRefresh: false,
  execMode: 'grid', tf: localStorage.getItem('hb-tf') === '1m' ? '1m' : '5m',
  chart: null, series: null, markers: null, trailSeries: null,
  priceLines: [], mcharts: [], centerRange: null,
  // Защита от гонки: старые ответы review/matrix не должны перерисовывать чужой сигнал.
  reviewSeq: 0, matrixSeq: 0, signalsSeq: 0,
};

// Окна — по времени: M5-значения в барах умножаются на 5 для M1.
function tfScale() { return state.tf === '1m' ? 5 : 1; }
function tfLabel() { return state.tf === '1m' ? 'M1' : 'M5'; }
function ctxBars(base) { return base * tfScale(); }
function lookforwardBars(base) { return base * tfScale(); }

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

// TP в архиве — JSON-массив строкой ('[0.3683]'), берём первую цену.
function firstPrice(v) {
  if (v === null || v === undefined || v === '') return null;
  let n;
  try {
    const p = typeof v === 'string' ? JSON.parse(v) : v;
    n = Number(Array.isArray(p) ? p[0] : p);
  } catch (_) { n = Number(v); }
  return Number.isFinite(n) ? n : null;
}
// PnL-знак относительно уровня: LONG (px-lvl), SHORT (lvl-px), в % от уровня.
function pctSigned(px, lvl, side) {
  if (px === null || !Number.isFinite(lvl) || lvl === 0) return null;
  const m = String(side || '').toUpperCase() === 'SHORT' ? -1 : 1;
  return (m * (px - lvl) / Math.abs(lvl)) * 100;
}
function fmtPct(p) {
  if (p === null || !Number.isFinite(p)) return '';
  return ` (${p >= 0 ? '+' : ''}${p.toFixed(2)}%)`;
}
function tpSlLine(s) {
  const lvl = Number(s.level_price);
  const tp = firstPrice(s.tp_arch);
  const slRaw = (s.sl_arch === null || s.sl_arch === undefined || s.sl_arch === '') ? null : Number(s.sl_arch);
  const sl = Number.isFinite(slRaw) ? slRaw : null;
  const tpTxt = tp !== null ? `${tp}${fmtPct(pctSigned(tp, lvl, s.side))}` : '—';
  const slTxt = sl !== null ? `${s.sl_arch}${fmtPct(pctSigned(sl, lvl, s.side))}` : '—';
  return `TP ${tpTxt} · SL ${slTxt}`;
}
// Потенциальный PnL ячейки: серверный pnl_pct, фолбэк — по входу/выходу.
function cellPnlPct(cell, side) {
  if (!cell) return null;
  if (cell.pnl_pct !== null && cell.pnl_pct !== undefined && cell.pnl_pct !== '') {
    const n = Number(cell.pnl_pct);
    if (Number.isFinite(n)) return n;
  }
  const e = Number(cell.entry_price), x = Number(cell.exit_price);
  if (!Number.isFinite(e) || !Number.isFinite(x) || e === 0) return null;
  let r = ((x - e) / e) * 100;
  if (String(side || '').toUpperCase() === 'SHORT') r = -r;
  return r;
}
function fmtPnlSigned(p) {
  const n = Number(p);
  if (!Number.isFinite(n)) return '—';
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`;
}
function pnlClass(p) {
  const n = Number(p);
  if (!Number.isFinite(n) || n === 0) return '';
  return n > 0 ? 'pnl-pos' : 'pnl-neg';
}
// Реальный (архивный) PnL сигнала: pnl_percent из БД, иначе — по entry/exit.
function archPnlPct(s) {
  if (s.pnl_pct_arch !== null && s.pnl_pct_arch !== undefined && s.pnl_pct_arch !== '') {
    const n = Number(s.pnl_pct_arch);
    if (Number.isFinite(n)) return n;
  }
  return pctSigned(
    s.exit_arch !== undefined ? Number(s.exit_arch) : null,
    Number(s.entry_arch),
    s.side,
  );
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
// Масштаб цены строго по факту: минимум набора — на нижней границе,
// максимум — на верхней (без центрирования по уровню и без отступов).
function calcFitRange(candles, extraPrices) {
  let mn = Infinity, mx = -Infinity;
  candles.forEach((c) => { mn = Math.min(mn, c.low); mx = Math.max(mx, c.high); });
  (extraPrices || []).forEach((v) => {
    if (v === null || v === undefined || v === '') return; // Number(null)===0 — иначе шкала схлопнется к нулю
    const n = Number(v);
    // PGv2 возвращает "0.0000000" для отсутствующего entry/exit — нулевая заглушка
    // иначе рисует ось от 0 и подписи вроде 0.0000006 вместо 0.0476.
    if (!Number.isFinite(n) || n === 0) return;
    mn = Math.min(mn, n); mx = Math.max(mx, n);
  });
  if (!Number.isFinite(mn) || !Number.isFinite(mx)) return null;
  if (mn >= mx) { const e = Math.abs(mx) * 0.0005 || 0.000001; mn -= e; mx += e; }
  return { minValue: mn, maxValue: mx };
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
  state.reviewSeq += 1; state.matrixSeq += 1; state.signalsSeq += 1;
  const mySeq = state.signalsSeq;
  const stopTake = $('f-outcome').value === 'STOP_TAKE';
  // STOP TAKE — отработавшие (закрытые по стопу или тейку, любая сторона):
  // сторону уважаем как выбрал пользователь, исход дофильтровываем на клиенте.
  const q = new URLSearchParams({
    search: $('f-search').value.trim(),
    side: $('f-side').value,
    limit: '200',
    sort: $('f-sort').value,
  });
  if ($('f-from').value) q.set('date_from', $('f-from').value);
  if ($('f-to').value) q.set('date_to', $('f-to').value);
  const d = await api('/api/hourbounce/signals?' + q.toString());
  // Защита от гонки: старый ответ поиска не перезаписывает новый список.
  if (mySeq !== state.signalsSeq) return;
  let items = d.items || [];
  const fo = $('f-outcome').value;
  if (stopTake) items = items.filter((s) => ['STOP', 'TAKE'].includes(String(s.outcome_arch || '').toUpperCase()));
  else if (fo) items = items.filter((s) => s.outcome_arch === fo);
  state.signals = items;
  $('sig-count').textContent = `сигналов: ${items.length} (показаны первые 200)`;
  const box = $('siglist');
  box.innerHTML = '';
  items.forEach((s, i) => {
    const div = document.createElement('div');
    div.className = 'sigrow' + (state.sel && state.sel.signal_id === s.signal_id ? ' sel' : '');
    const archPnl = archPnlPct(s);
    const archPnlTxt = archPnl !== null
      ? `<span class="${pnlClass(archPnl)}">PnL арх. ${fmtPnlSigned(archPnl)}</span>`
      : '<span style="color:#5a636e">PnL арх. —</span>';
    const realTag = s.real_trade ? ` · real ${s.real_trade.entry}·SL${s.real_trade.sl_index}` : '';
    div.innerHTML = `<div><div class="nm">${s.symbol} · ${s.side} · ${s.signal_id}</div>
      <div class="mt">lvl ${s.level_price} · выставлен ${s.dt_place || ''} UTC<br>${tpSlLine(s)}<br>${archPnlTxt}${realTag}<br>touch PGv2 ${s.touch_ref ? s.touch_ref.replace('T', ' ').replace('Z', ' UTC') : '—'}</div></div>
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
    b.onclick = () => { state.entry = b.dataset.e; setTabs(); state.reviewSeq++; loadReview(); };
  });
  document.querySelectorAll('#tabs-sl button').forEach((b) => {
    b.classList.toggle('on', Number(b.dataset.s) === state.sl);
    b.onclick = () => { state.sl = Number(b.dataset.s); setTabs(); state.reviewSeq++; loadReview(); };
  });
  document.querySelectorAll('#tabs-tf button').forEach((b) => {
    b.classList.toggle('on', b.dataset.tf === state.tf);
    b.onclick = () => {
      if (state.tf === b.dataset.tf) return;
      state.tf = b.dataset.tf;
      localStorage.setItem('hb-tf', state.tf);
      setTabs();
      updateTfLabels();
      state.reviewSeq++; state.matrixSeq++;
      if (state.mode === 'matrix') loadMatrix();
      else loadReview();
    };
  });
}

function updateTfLabels() {
  const lbl = tfLabel();
  const hs = $('head-sub');
  if (hs) hs.textContent = `архив PGv2 · ${lbl} · T1M/T1L/T2/T3 × SL 0.5/1.0/1.5`;
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
      state.reviewSeq++;
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
  const seq = ++state.reviewSeq;
  $('res-line').textContent = `${s.symbol} · ${s.side} · level ${s.level_price} · ${s.dt_place || ''} — загрузка...`;
  $('banner').classList.remove('on');
  const rf = state.forceRefresh ? '&refresh=1' : '';
  state.forceRefresh = false;
  let d;
  try {
    d = await api(`/api/hourbounce/review?signal_id=${encodeURIComponent(s.signal_id)}&entry=${state.entry}&sl=${state.sl}&mode=${state.execMode}&pre=${ctxBars(51)}&post=${ctxBars(51)}&lookforward=${lookforwardBars(2000)}&tf=${state.tf}${rf}`);
  } catch (e) {
    $('res-line').textContent = 'ошибка: ' + e.message;
    return;
  }
  // Защита от гонки: старый ответ (другой сигнал/настройки) не перерисовывает график.
  if (seq !== state.reviewSeq || state.sel?.signal_id !== s.signal_id || state.mode !== 'single') return;
  if (d.signal) {
    state.sel = { ...state.sel, price_precision: d.signal.price_precision, tick_size: d.signal.tick_size };
  }
  renderReview(d);
}

function renderReview(d) {
  ensureChart();
  const { signal, params, result, candles } = d;
  state.centerRange = calcFitRange(candles,
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
  const rPnl = cellPnlPct(result, signal.side);
  $('res-line').textContent = `${signal.symbol} · ${signal.side} · ${tfLabel()} · ${state.entry}/SL${params.sl_index} · вход ${fmtP(result.entry_price, signal)} · выход ${fmtP(result.exit_price, signal)} · PnL ${fmtPnlSigned(rPnl)} · R ${result.r_multiple || '—'} · touch наш ${shortDt(result.touch_dt)} · PGv2 ${shortDt(signal.touch_ref)}${d.cache && d.cache.cells_hit ? ' · из кеша' : ''}`;
  const bn = $('banner');
  if (result.mode === 'placement') {
    bn.innerHTML = `С момента выставления уровня (<b>${signal.dt_place} UTC</b>) касания не было — ситуация не отработала во всей загруженной истории. ` +
      `Touch PGv2: <b>${shortDt(signal.touch_ref)}</b>.`;
    bn.classList.add('on');
  } else {
    bn.classList.remove('on');
  }
  $('head-sub').textContent = `сигнал ${signal.symbol} ${signal.signal_id} · level ${signal.level_price} · ${signal.side} · арх. ${state.sel.outcome_arch}`;

  const hasC = (result.confirm_dt || []).length > 0;
  const s4 = result.outcome === 'TAKE' ? 'ok-take' : result.outcome === 'STOP' ? 'ok-stop' : 'on';
  const s4label = result.outcome === 'TAKE' ? (result.exit_kind === 'tp' ? 'TP' : 'trail') : result.outcome === 'STOP' ? 'SL' : result.outcome;
  $('stepper').innerHTML = `
    <div class="step on"><div class="dot">1</div>touch ${result.touch_dt ? '✓' : '—'}</div>
    <div class="step ${hasC === null ? 'na' : hasC ? 'on' : ''}"><div class="dot">2</div>${hasC === null ? 'N/A' : 'confirm'}</div>
    <div class="step ${result.entry_dt ? 'on' : 'na'}"><div class="dot">3</div>entry</div>
    <div class="step ${s4}"><div class="dot">4</div>${s4label}</div>`;
  const ex = d.exec;
  $('exec-line').textContent = ex ? `PG: SL ${ex.sl} · TP ${ex.tp || '—'} · БУ ${ex.be || '—'} · trail ${ex.trail || '—'}${ex.trail_tp_only ? ' (только trail)' : ''}` : '';
  $('metrics').innerHTML = `<span>PnL <b class="${pnlClass(rPnl)}">${fmtPnlSigned(rPnl)}</b></span><span>R <b>${result.r_multiple || '—'}</b></span><span>max+ <b>${result.max_profit_pct || '—'}%</b></span><span>MAE <b>${result.mae_pct || '—'}%</b></span><span>MFE <b>${result.mfe_pct || '—'}%</b></span><span>баров <b>${result.bars_in_trade}</b></span><span>trail точек <b>${(result.trail_path || []).length}</b></span>`;
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
  state.reviewSeq++; state.matrixSeq++;
  if (m === 'matrix') loadMatrix();
  else loadReview();
}
$('m-single').onclick = () => setMode('single');
$('m-matrix').onclick = () => setMode('matrix');
$('m-export').onclick = () => {
  if (!state.sel) return;
  window.open('/api/hourbounce/export?signal_id=' + encodeURIComponent(state.sel.signal_id) + `&lookforward=${lookforwardBars(2000)}&tf=${state.tf}`, '_blank');
};
$('m-refresh').onclick = () => {
  if (!state.sel) return;
  state.forceRefresh = true;
  state.reviewSeq++; state.matrixSeq++;
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
  const seq = ++state.matrixSeq;
  $('res-line').textContent = `${s.symbol} · матрица 12 комбинаций — загрузка...`;
  $('banner').classList.remove('on');
  const rf = state.forceRefresh ? '&refresh=1' : '';
  state.forceRefresh = false;
  let d;
  try {
    d = await api(`/api/hourbounce/matrix?signal_id=${encodeURIComponent(s.signal_id)}&pre=${ctxBars(51)}&post=${ctxBars(51)}&lookforward=${lookforwardBars(2000)}&tf=${state.tf}${rf}`);
  } catch (e) {
    $('res-line').textContent = 'ошибка: ' + e.message;
    return;
  }
  // Защита от гонки: старый ответ (другой сигнал/настройки) не перерисовывает матрицу.
  if (seq !== state.matrixSeq || state.sel?.signal_id !== s.signal_id || state.mode !== 'matrix') return;
  if (d.signal) {
    state.sel = { ...state.sel, price_precision: d.signal.price_precision, tick_size: d.signal.tick_size };
  }
  destroyMatrix();
  const box = $('matrix');
  const real = d.real_trade || null;
  state.realTrade = real;
  const counts = { TAKE: 0, STOP: 0, NO_ENTRY: 0, EXPIRED: 0 };
  d.cells.forEach((c) => { counts[c.outcome] = (counts[c.outcome] || 0) + 1; });
  // Самый выгодный вариант: max PnL среди закрытых (TAKE/STOP).
  // Зелёная рамка — лучший; красная — наименьший убыток, если все 9 закрылись по стопу.
  const mSide = String((d.signal && d.signal.side) || (s.side) || '').toUpperCase();
  const pnlOf = (c) => {
    const e = Number(c.entry_price), x = Number(c.exit_price);
    if (!Number.isFinite(e) || !Number.isFinite(x) || e === 0) return null;
    let r = ((x - e) / e) * 100;
    if (mSide === 'SHORT') r = -r;
    return r;
  };
  const closed = d.cells
    .map((c) => ({ c, pnl: pnlOf(c) }))
    .filter((o) => o.pnl !== null && (o.c.outcome === 'TAKE' || o.c.outcome === 'STOP'));
  let bestKey = null, bestRed = false;
  if (closed.length) {
    closed.sort((a, b) => b.pnl - a.pnl);
    bestKey = closed[0].c.entry + '|' + closed[0].c.sl_index;
    bestRed = d.cells.every((c) => c.outcome === 'STOP');
  }
  const realTxt = real
    ? `реальная сделка <b style="color:#f7c948">★ ${real.entry}·SL${real.sl_index}</b> · арх. PnL <b>${fmtPnlSigned(real.pnl_pct_arch)}</b>`
    : 'реальная сделка <b>—</b> (в архиве нет закрытой сделки)';
  $('agg').innerHTML = `<span>winrate <b>${Math.round((counts.TAKE / 9) * 100)}%</b></span>
    <span>TAKE <b>${counts.TAKE}</b></span><span>STOP <b>${counts.STOP}</b></span>
    <span>NO_ENTRY <b>${counts.NO_ENTRY}</b></span><span>EXPIRED <b>${counts.EXPIRED}</b></span>
    <span>арх. исход <b>${s.outcome_arch}</b></span>
    <span>${realTxt}</span>
    <span>${d.cache && d.cache.cells_hit ? 'из кеша' : 'посчитано'} · свечей из кеша <b>${(d.cache && d.cache.candles_cached) ?? '—'}</b></span>
    <span>окно: от выставления до ближайшей отработки · всё время UTC</span>`;
  $('res-badge').textContent = `${counts.TAKE}T / ${counts.STOP}S / ${counts.NO_ENTRY}NE`;
  $('res-line').textContent = `${s.symbol} · ${s.side} · level ${s.level_price} · ${tfLabel()} · 12 комбинаций · touch PGv2 ${shortDt(d.signal.touch_ref)}`;

  box.appendChild(document.createElement('div'));
  const slSizes = d.sl_sizes || ['0.5', '1.0', '1.5'];
  [1, 2, 3].forEach((sl) => {
    const el = document.createElement('div');
    el.className = 'colh';
    el.textContent = `SL${sl} · ${slSizes[sl - 1]}%`;
    box.appendChild(el);
  });
  const labels = { T1M: 'T1M · маркет M1', T1L: 'T1L · лимит M1', T2: 'T2 · 1 бар', T3: 'T3 · 2 бара' };
  let syncing = false;
  ['T1M', 'T1L', 'T2', 'T3'].forEach((code) => {
    const rh = document.createElement('div');
    rh.className = 'rowh';
    rh.textContent = labels[code];
    box.appendChild(rh);
    [1, 2, 3].forEach((sl) => {
      const cell = d.cells.find((c) => c.entry === code && c.sl_index === sl);
      if (!cell) { console.warn('[hourbounce] нет ячейки', code, 'SL' + sl, '— пропуск (версии фронта/бэка расходятся?)'); return; }
      const card = document.createElement('div');
      const hl = (code + '|' + sl) === bestKey ? (bestRed ? ' best-stop' : ' best-take') : '';
      const isReal = !!(real && real.entry === code && real.sl_index === sl);
      card.className = 'mcard' + hl + (isReal ? ' real-trade' : '');
      if (isReal) card.title = `Реальная сделка PGv2: ${real.entry}·SL${real.sl_index}, арх. PnL ${fmtPnlSigned(real.pnl_pct_arch)}`;
      const cls = { TAKE: 'b-take', STOP: 'b-stop', NO_ENTRY: 'b-noentry', EXPIRED: 'b-expired' }[cell.outcome];
      const cp = cellPnlPct(cell, (d.signal && d.signal.side) || s.side);
      card.innerHTML = `<div class="mhead"><span>${code}·SL${sl} · ${tfLabel()}${isReal ? ' <span class="real-tag">★ real</span>' : ''}</span><span class="badge ${cls}">${cell.outcome}</span></div><div class="mchart"></div><div class="mfoot">in ${cell.entry_price || '—'} · out ${cell.exit_price || '—'} · R ${cell.r_multiple || '—'} · <span class="${pnlClass(cp)}">PnL ${fmtPnlSigned(cp)}</span></div>`;
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
      const range = calcFitRange(slice,
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
updateTfLabels();
setMode('matrix');
loadSignals();
fetch('/api/version').then((r) => r.json()).then((d) => {
  if (d && d.version) $('app-version').textContent = 'v' + d.version;
}).catch(() => {});
