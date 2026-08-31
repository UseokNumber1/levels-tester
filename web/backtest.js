(function () {
  'use strict';

  // --- DOM refs ---
  const symbolSel = document.getElementById('bt-symbol');
  const periodFrom = document.getElementById('bt-period-from');
  const periodTo = document.getElementById('bt-period-to');
  const sideSel = document.getElementById('bt-side');
  const loadBtn = document.getElementById('bt-load');
  const signalsCount = document.getElementById('bt-signals-count');
  const signalsBody = document.getElementById('bt-signals-body');
  const selAllCb = document.getElementById('bt-sel-all');
  const runBtn = document.getElementById('bt-run');
  const progressWrap = document.getElementById('bt-progress-wrap');
  const progressFill = document.getElementById('bt-progress-fill');
  const progressText = document.getElementById('bt-progress-text');
  const errorDiv = document.getElementById('bt-error');
  const variantsGrid = document.getElementById('bt-variants-grid');
  const resultsWrap = document.getElementById('bt-results-wrap');
  const limitField = document.getElementById('bt-limit-field');
  const confField = document.getElementById('bt-conf-field');
  const confWaitField = document.getElementById('bt-conf-wait-field');

  let allSignals = [];
  let variants = [];
  let currentResults = null;
  let equityChart = null;
  let pnlChart = null;

  // --- Entry type radio ---
  document.querySelectorAll('input[name="bt-entry"]').forEach(r => {
    r.addEventListener('change', () => {
      limitField.classList.toggle('hidden', r.value !== 'limit');
      confField.classList.toggle('hidden', r.value !== 'confirmation');
      confWaitField.classList.toggle('hidden', r.value !== 'confirmation');
    });
  });

  // --- Load signals ---
  loadBtn.addEventListener('click', async () => {
    loadBtn.disabled = true;
    loadBtn.textContent = 'Loading...';
    try {
      const period = periodFrom.value && periodTo.value
        ? periodFrom.value + ':' + periodTo.value
        : '';
      const sym = symbolSel.value;
      const sid = sideSel.value.toLowerCase();
      const resp = await fetch(`/api/backtest/signals?limit=500${sym ? '&symbol=' + sym : ''}${sid ? '&side=' + sid : ''}${period ? '&period=' + period : ''}`);
      const data = await resp.json();
      allSignals = data.items || [];
      signalsCount.textContent = `Loaded ${allSignals.length} signals`;
      renderSignals();
      // Also load symbols for filter
      loadSymbols();
    } catch (e) {
      signalsCount.textContent = 'Error loading signals: ' + e.message;
    } finally {
      loadBtn.disabled = false;
      loadBtn.textContent = 'Load Signals';
    }
  });

  function renderSignals() {
    signalsBody.innerHTML = '';
    allSignals.forEach((s, i) => {
      const tr = document.createElement('tr');
      const pnl = s.entry_price - s.stop_loss;
      const rr = s.side === 'LONG' ? (s.entry_price / s.stop_loss - 1) : (1 - s.entry_price / s.stop_loss);
      tr.innerHTML = `<td><input type="checkbox" data-idx="${i}"></td>
        <td>${s.symbol}</td>
        <td class="${s.side === 'LONG' ? 'pos' : 'neg'}">${s.side}</td>
        <td>${s.entry_price}</td>
        <td>${s.stop_loss}</td>
        <td>${Math.abs(rr).toFixed(1)}</td>
        <td>${s.timestamp || ''}</td>
        <td>${s.source || ''}</td>`;
      signalsBody.appendChild(tr);
    });
    updateRunBtn();
  }

  selAllCb.addEventListener('change', () => {
    signalsBody.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      cb.checked = selAllCb.checked;
    });
    updateRunBtn();
  });

  signalsBody.addEventListener('change', updateRunBtn);

  function getSelectedSignalIds() {
    const ids = [];
    signalsBody.querySelectorAll('input[type="checkbox"]:checked').forEach(cb => {
      const idx = parseInt(cb.dataset.idx);
      if (allSignals[idx]) ids.push(allSignals[idx].signal_id);
    });
    return ids;
  }

  function updateRunBtn() {
    runBtn.disabled = getSelectedSignalIds().length === 0;
  }

  // --- Load symbols ---
  async function loadSymbols() {
    try {
      const resp = await fetch('/api/backtest/symbols');
      const data = await resp.json();
      symbolSel.innerHTML = '<option value="">All symbols</option>';
      (data.items || []).forEach(s => {
        const opt = document.createElement('option');
        opt.value = s;
        opt.textContent = s;
        symbolSel.appendChild(opt);
      });
    } catch (e) { /* ignore */ }
  }

  // --- Load variants ---
  async function loadVariants() {
    try {
      const resp = await fetch('/api/backtest/variants');
      const data = await resp.json();
      variants = data.items || [];
      variantsGrid.innerHTML = '';
      variants.forEach(v => {
        const card = document.createElement('label');
        card.className = 'variant-card';
        let tags = '';
        if (v.trailing) tags += '<span class="tag tag-trail">T</span>';
        if (v.breakeven) tags += '<span class="tag tag-be">BE</span>';
        if (v.partial) tags += '<span class="tag tag-partial">PC</span>';
        card.innerHTML = `<input type="checkbox" value="${v.id}" ${variants.indexOf(v) < 3 ? 'checked' : ''}>
          <span>${v.name}</span>
          <span class="tags">SL ${v.sl_pct}%${v.tp_rr ? ' / RR ' + v.tp_rr : ''} ${tags}</span>`;
        variantsGrid.appendChild(card);
      });
    } catch (e) { /* ignore */ }
  }

  // --- Run backtest ---
  runBtn.addEventListener('click', startBacktest);

  async function startBacktest() {
    const signalIds = getSelectedSignalIds();
    if (signalIds.length === 0) return;

    const selectedVariants = [];
    variantsGrid.querySelectorAll('input:checked').forEach(cb => {
      selectedVariants.push(cb.value);
    });
    if (selectedVariants.length === 0) {
      errorDiv.textContent = 'Select at least one variant';
      errorDiv.classList.remove('hidden');
      return;
    }

    const entryType = document.querySelector('input[name="bt-entry"]:checked').value;

    runBtn.disabled = true;
    errorDiv.classList.add('hidden');
    progressWrap.classList.remove('hidden');
    resultsWrap.classList.add('hidden');
    progressFill.style.width = '0%';
    progressText.textContent = 'Starting...';

    try {
      const body = {
        signal_ids: signalIds,
        variants: selectedVariants,
        entry_type: entryType,
        limit_offset: parseFloat(document.getElementById('bt-limit-offset').value),
        confirmation_bars: parseInt(document.getElementById('bt-conf-bars').value),
        confirmation_max_wait: parseInt(document.getElementById('bt-conf-wait').value),
        lookback: parseInt(document.getElementById('bt-lookback').value),
        lookforward: parseInt(document.getElementById('bt-lookforward').value),
      };

      const resp = await fetch('/api/backtest/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await resp.json();
      if (resp.status === 202) {
        pollJob(data.job_id);
      } else {
        throw new Error(data.detail || 'Failed to start backtest');
      }
    } catch (e) {
      errorDiv.textContent = e.message;
      errorDiv.classList.remove('hidden');
      runBtn.disabled = false;
    }
  }

  async function pollJob(jobId) {
    try {
      const resp = await fetch(`/api/backtest/status/${jobId}`);
      const job = await resp.json();

      if (job.status === 'running' || job.status === 'pending') {
        const pct = job.total > 0 ? Math.round((job.progress / job.total) * 100) : 0;
        progressFill.style.width = pct + '%';
        progressText.textContent = `${job.progress}/${job.total} — ${job.current_symbol || '...'}`;
        setTimeout(() => pollJob(jobId), 500);
        return;
      }

      if (job.status === 'failed') {
        errorDiv.textContent = 'Backtest failed: ' + (job.error || 'unknown error');
        errorDiv.classList.remove('hidden');
        runBtn.disabled = false;
        return;
      }

      if (job.status === 'completed') {
        progressFill.style.width = '100%';
        progressText.textContent = 'Done!';
        runBtn.disabled = false;
        await loadResults(jobId);
      }
    } catch (e) {
      errorDiv.textContent = 'Error polling job: ' + e.message;
      errorDiv.classList.remove('hidden');
      runBtn.disabled = false;
    }
  }

  async function loadResults(jobId) {
    const resp = await fetch(`/api/backtest/report/${jobId}`);
    currentResults = await resp.json();
    resultsWrap.classList.remove('hidden');
    renderMetrics(currentResults.metrics);
    renderTrades(currentResults.trades);
    renderEquity(currentResults.metrics);
    renderPnlBar(currentResults.metrics);
  }

  // --- Render metrics ---
  function renderMetrics(metrics) {
    const tbody = document.getElementById('bt-metrics-body');
    tbody.innerHTML = '';
    if (!metrics || metrics.length === 0) return;

    const bestPnl = Math.max(...metrics.map(m => m.total_pnl));
    metrics.forEach(m => {
      const tr = document.createElement('tr');
      if (m.total_pnl === bestPnl) tr.className = 'best';
      tr.innerHTML = `<td>${m.variant_name}</td>
        <td>${m.total_trades}</td>
        <td class="${m.winrate >= 50 ? 'pos' : 'neg'}">${m.winrate}%</td>
        <td class="${m.total_pnl >= 0 ? 'pos' : 'neg'}">${m.total_pnl >= 0 ? '+' : ''}${m.total_pnl.toFixed(4)}</td>
        <td>${m.profit_factor === 'Infinity' ? '∞' : m.profit_factor}</td>
        <td class="neg">${m.max_drawdown.toFixed(4)}</td>
        <td class="pos">${m.avg_win >= 0 ? '+' : ''}${m.avg_win.toFixed(4)}</td>
        <td class="neg">${m.avg_loss.toFixed(4)}</td>
        <td>${m.expectancy.toFixed(4)}</td>
        <td>${m.avg_bars_held}</td>`;
      tbody.appendChild(tr);
    });

    // Side stats
    const sideStats = document.getElementById('bt-side-stats');
    sideStats.innerHTML = '';
    metrics.forEach(m => {
      const div = document.createElement('div');
      div.innerHTML = `<div style="background:#16213e;padding:8px 12px;border-radius:6px;border-left:3px solid #2196F3;">
        <div style="font-size:11px;color:#888;">${m.variant_name}</div>
        <div>LONG: <span class="${m.long_winrate >= 50 ? 'pos' : 'neg'}">${m.long_winrate}%</span> (${m.long_trades} trades)</div>
        <div>SHORT: <span class="${m.short_winrate >= 50 ? 'pos' : 'neg'}">${m.short_winrate}%</span> (${m.short_trades} trades)</div>
      </div>`;
      sideStats.appendChild(div);
    });

    // Symbol stats
    const symbolBody = document.getElementById('bt-symbol-body');
    symbolBody.innerHTML = '';
    if (currentResults.trades) {
      const bySymbol = {};
      currentResults.trades.forEach(t => {
        if (!bySymbol[t.symbol]) bySymbol[t.symbol] = { wins: 0, losses: 0, pnl: 0 };
        if (t.pnl > 0) bySymbol[t.symbol].wins++;
        else bySymbol[t.symbol].losses++;
        bySymbol[t.symbol].pnl += t.pnl;
      });
      Object.keys(bySymbol).sort((a, b) => bySymbol[b].pnl - bySymbol[a].pnl).forEach(sym => {
        const d = bySymbol[sym];
        const total = d.wins + d.losses;
        const wr = total > 0 ? Math.round((d.wins / total) * 100) : 0;
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${sym}</td><td>${total}</td>
          <td class="${wr >= 50 ? 'pos' : 'neg'}">${wr}%</td>
          <td class="${d.pnl >= 0 ? 'pos' : 'neg'}">${d.pnl >= 0 ? '+' : ''}${d.pnl.toFixed(4)}</td>`;
        symbolBody.appendChild(tr);
      });
    }
  }

  // --- Render equity curve ---
  function renderEquity(metrics) {
    if (equityChart) equityChart.destroy();
    const canvas = document.getElementById('bt-equity-chart');
    const ctx = canvas.getContext('2d');

    const datasets = metrics.map((m, i) => {
      const hue = (i * 360 / metrics.length) % 360;
      const data = m.equity_curve || [];
      return {
        label: m.variant_name,
        data: data.map((v, idx) => ({ x: idx, y: v })),
        borderColor: `hsl(${hue}, 70%, 50%)`,
        borderWidth: 1.5,
        pointRadius: 0,
        fill: false,
        tension: 0,
      };
    });

    equityChart = new Chart(ctx, {
      type: 'line',
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        scales: {
          x: { title: { display: true, text: 'Trade #', color: '#888' }, ticks: { color: '#888' }, grid: { color: '#222' } },
          y: { title: { display: true, text: 'Equity (USDT)', color: '#888' }, ticks: { color: '#888' }, grid: { color: '#222' } },
        },
        plugins: {
          legend: { labels: { color: '#ccc', font: { size: 11 } } },
        },
      },
    });
  }

  // --- Render PnL bar ---
  function renderPnlBar(metrics) {
    if (pnlChart) pnlChart.destroy();
    const canvas = document.getElementById('bt-pnl-chart');
    const ctx = canvas.getContext('2d');

    pnlChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: metrics.map(m => m.variant_name),
        datasets: [{
          label: 'Total PnL',
          data: metrics.map(m => m.total_pnl),
          backgroundColor: metrics.map(m => m.total_pnl >= 0 ? '#4CAF50' : '#f44336'),
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        indexAxis: 'y',
        scales: {
          x: { ticks: { color: '#888' }, grid: { color: '#222' } },
          y: { ticks: { color: '#ccc', font: { size: 11 } }, grid: { display: false } },
        },
        plugins: {
          legend: { display: false },
        },
      },
    });
  }

  // --- Render trades ---
  function renderTrades(trades) {
    if (!trades) return;
    populateTradeFilters(trades);
    displayTrades(trades);

    document.getElementById('bt-f-symbol').addEventListener('change', () => filterTrades(trades));
    document.getElementById('bt-f-side').addEventListener('change', () => filterTrades(trades));
    document.getElementById('bt-f-result').addEventListener('change', () => filterTrades(trades));
    document.getElementById('bt-f-variant').addEventListener('change', () => filterTrades(trades));
  }

  function populateTradeFilters(trades) {
    const symbols = [...new Set(trades.map(t => t.symbol))].sort();
    const variantIds = [...new Set(trades.map(t => t.variant_id))];
    const symSel = document.getElementById('bt-f-symbol');
    const varSel = document.getElementById('bt-f-variant');

    symSel.innerHTML = '<option value="">All symbols</option>';
    symbols.forEach(s => { const o = document.createElement('option'); o.value = s; o.textContent = s; symSel.appendChild(o); });

    varSel.innerHTML = '<option value="">All variants</option>';
    if (currentResults && currentResults.metrics) {
      currentResults.metrics.forEach(m => { const o = document.createElement('option'); o.value = m.variant_id; o.textContent = m.variant_name; varSel.appendChild(o); });
    }
  }

  function filterTrades(allTrades) {
    const sym = document.getElementById('bt-f-symbol').value;
    const side = document.getElementById('bt-f-side').value;
    const result = document.getElementById('bt-f-result').value;
    const varId = document.getElementById('bt-f-variant').value;

    let filtered = allTrades;
    if (sym) filtered = filtered.filter(t => t.symbol === sym);
    if (side) filtered = filtered.filter(t => t.side === side);
    if (result === 'win') filtered = filtered.filter(t => t.pnl > 0);
    if (result === 'loss') filtered = filtered.filter(t => t.pnl <= 0);
    if (varId) filtered = filtered.filter(t => t.variant_id === varId);
    displayTrades(filtered);
  }

  function displayTrades(trades) {
    const tbody = document.getElementById('bt-trades-body');
    tbody.innerHTML = '';
    trades.forEach((t, i) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${i + 1}</td>
        <td>${t.symbol}</td>
        <td class="${t.side === 'LONG' ? 'pos' : 'neg'}">${t.side}</td>
        <td>${t.entry_price}</td>
        <td>${t.exit_price || '-'}</td>
        <td class="${t.pnl >= 0 ? 'pos' : 'neg'}">${t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(4)}</td>
        <td class="${t.pnl_pct >= 0 ? 'pos' : 'neg'}">${t.pnl_pct.toFixed(2)}%</td>
        <td>${t.bars_held}</td>
        <td>${t.exit_reason || '-'}</td>
        <td style="font-size:11px;color:#888;">${t.variant_name}</td>`;
      tbody.appendChild(tr);
    });
  }

  // --- Tabs ---
  document.querySelectorAll('.results-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.results-tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      tab.classList.add('active');
      const target = tab.dataset.tab;
      document.getElementById('tab-' + target).classList.add('active');
    });
  });

  // --- Init ---
  loadVariants();
  loadSymbols();
})();
