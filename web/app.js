const state = { runId: null, chart: null, candleSeries: null, detailSequence: null };
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

async function checkServer() {
  try {
    const status = await request('/api/status');
    const connected = status.backend === 'connected' && status.database === 'connected' && status.database_schema === 'valid';
    $('server-dot').className = `dot ${connected ? 'ok' : 'bad'}`;
    $('server-status').textContent = `SERVER ${connected ? 'CONNECTED' : 'DEGRADED'} · SQL ${status.database_timezone} · BINANCE ${status.binance.toUpperCase()}`;
  } catch (_) { $('server-dot').className = 'dot bad'; $('server-status').textContent = 'SERVER DISCONNECTED'; }
}

async function loadInstruments(force = false) {
  const params = new URLSearchParams({ search:$('instrument-search').value, limit:'100', refresh:String(force) });
  if ($('min-volume').value) params.set('min_volume', $('min-volume').value);
  try {
    const data = await request(`/api/instruments?${params}`);
    $('symbol').innerHTML = data.items.length ? data.items.map(item => `<option value="${item.symbol}">${item.symbol} · ${formatVolume(item.daily_volume_usdt)}</option>`).join('') : '<option>No instruments</option>';
    $('instrument-info').textContent = `${data.count} USDT instruments`;
  } catch (error) { $('instrument-info').textContent = error.message; }
}

function render(snapshot) {
  state.runId = snapshot.run_id;
  const isLoading = snapshot.status === 'loading';
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
  $('cursor').textContent = snapshot.cursor ? new Date(snapshot.cursor.bar_time).toLocaleString() : 'not started';
  $('bar-count').textContent = `${snapshot.master_candles.length} bars`;
  $('detail-count').textContent = `${snapshot.detail_candles.length} candles`;
  $('level-count').textContent = snapshot.levels.length;
  $('levels').classList.toggle('empty', !snapshot.levels.length);
  $('levels').innerHTML = snapshot.levels.length ? snapshot.levels.map(level => `
    <div class="level"><div class="level-title ${level.side}"><span>${level.side}</span><span>${level.price}</span></div>
    <div class="level-meta">${level.state} · ${level.touch_count} touches · zone ${level.zone_low}–${level.zone_high}</div></div>`).join('') : 'No levels confirmed yet.';
  $('events').classList.toggle('empty', !snapshot.events.length);
  $('events').innerHTML = snapshot.events.length ? snapshot.events.slice().reverse().map(event => `
    <div class="event"><span class="event-time">${new Date(event.event_time).toLocaleString()}</span><span class="event-type">${event.event_type}</span><span>${event.reason}</span></div>`).join('') : 'Events will appear after Step.';
  draw(snapshot.master_candles, snapshot.levels, snapshot.pivots);
  loadDetail(snapshot);
}

async function loadDetail(snapshot) {
  if (!snapshot.cursor || state.detailSequence === snapshot.cursor.sequence) return;
  state.detailSequence = snapshot.cursor.sequence;
  const end = new Date(snapshot.cursor.bar_time);
  const start = new Date(end.getTime() - 60 * 60 * 1000);
  $('detail').textContent = 'Loading detail candles...';
  try {
    const detail = await request(`/api/runs/${snapshot.run_id}/detail?start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`);
    $('detail-count').textContent = `${detail.detail_candles.length} candles`;
    $('detail').innerHTML = detail.detail_candles.length ? detail.detail_candles.slice(-8).map(candle => `<div class="event"><span class="event-time">${new Date(candle.open_time).toLocaleTimeString()}</span><span>O ${candle.open} · H ${candle.high} · L ${candle.low} · C ${candle.close}</span></div>`).join('') : 'No detail candles in this range.';
  } catch (error) { $('detail').textContent = error.message; }
}

function draw(candles, levels = [], pivots = []) {
  if (!window.LightweightCharts || !$('chart')) return;
  if (!state.chart) { state.chart = LightweightCharts.createChart($('chart'), { layout:{ background:{color:'#fffdf8'}, textColor:'#71808a' }, grid:{ vertLines:{color:'#eeeae1'}, horzLines:{color:'#eeeae1'} }, timeScale:{ timeVisible:true } }); state.candleSeries = state.chart.addCandlestickSeries({ upColor:'#198754', downColor:'#ee6c4d', borderVisible:false, wickUpColor:'#198754', wickDownColor:'#ee6c4d' }); }
  state.candleSeries.setData(candles.map(c => ({ time:Math.floor(new Date(c.open_time).getTime()/1000), open:Number(c.open), high:Number(c.high), low:Number(c.low), close:Number(c.close) })));
  state.levelLines?.forEach(line => state.candleSeries.removePriceLine(line));
  state.levelLines = levels.filter(level => !['broken', 'expired'].includes(level.state)).map(level => state.candleSeries.createPriceLine({ price:Number(level.price), color:level.side === 'support' ? '#2166f3' : '#ee6c4d', lineWidth:1, lineStyle:2, axisLabelVisible:true, title:level.side[0].toUpperCase() }));
  if (state.candleSeries.setMarkers) state.candleSeries.setMarkers(pivots.map(pivot => ({ time:Math.floor(new Date(pivot.pivot_time).getTime()/1000), position:pivot.kind === 'high' ? 'aboveBar' : 'belowBar', color:pivot.kind === 'high' ? '#ee6c4d' : '#2166f3', shape:pivot.kind === 'high' ? 'arrowDown' : 'arrowUp', text:pivot.kind } )));
  state.chart.timeScale().fitContent();
}

async function request(url, options = {}) { const response = await fetch(url, { headers:{'Content-Type':'application/json'}, ...options }); const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Request failed'); return data; }
async function command(name) { if (!state.runId) return; try { render(await request(`/api/runs/${state.runId}/${name}`, { method:'POST' })); } catch (error) { alert(error.message); } }

$('create').onclick = async () => {
  try { const dateStr = $('display-from').value + 'T00:00:00Z'; const snapshot = await request('/api/runs', { method:'POST', body:JSON.stringify({ symbol:$('symbol').value, display_from:dateStr, detail_timeframe:$('detail-timeframe').value }) }); render(snapshot); pollRun(snapshot.run_id); }
  catch (error) { alert(error.message); }
};
document.querySelectorAll('[data-command]').forEach(button => button.onclick = () => command(button.dataset.command));
$('speed').onchange = async () => { if (state.runId) render(await request(`/api/runs/${state.runId}/speed`, { method:'PATCH', body:JSON.stringify({speed:Number($('speed').value)}) })); };
$('refresh-instruments').onclick = () => loadInstruments(true);
$('instrument-search').onchange = () => loadInstruments(false);
$('min-volume').onchange = () => loadInstruments(false);
async function pollRun(runId) { for (;;) { await new Promise(resolve => setTimeout(resolve, 500)); const snapshot = await request(`/api/runs/${runId}`); render(snapshot); if (snapshot.status !== 'loading') break; } }
checkServer(); loadInstruments(false); setInterval(checkServer, 5000);
