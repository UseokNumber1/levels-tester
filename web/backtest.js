(function () {
  'use strict';

  // --- DOM refs ---
  const periodFrom = document.getElementById('bt-period-from');
  const periodTo = document.getElementById('bt-period-to');
  const symbolSearch = document.getElementById('bt-symbol-search');
  const symbolChecklist = document.getElementById('bt-symbol-checklist');
  const symbolChecklistCount = document.getElementById('bt-symbol-checklist-count');
  const symbolClear = document.getElementById('bt-symbol-clear');
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
  const variantsBody = document.getElementById('bt-variants-body');
  const varSelAllCb = document.getElementById('bt-var-sel-all');
  const addRowBtn = document.getElementById('bt-variant-add');
  const resetBtn = document.getElementById('bt-variant-reset');
  const variantCount = document.getElementById('bt-variant-count');
  const resultsWrap = document.getElementById('bt-results-wrap');
  const LS_KEY = 'bt-variant-rows-v2';

  let allSignals = [];
  let variantRows = []; // {id,name,sl_pct,tp_type,tp_value,trailing_activation_pct,... ,_selected}
  let currentResults = null;
  let currentMetrics = [];
  let currentBestBySymbol = [];
  let equityChart = null;
  let pnlChart = null;

  const GROUP_INPUTS = document.querySelectorAll('input[name="bt-group"]');

  function debugLog(message, details) {
    if (details === undefined) console.log(`[Backtest] ${message}`);
    else console.log(`[Backtest] ${message}`, details);
  }

  function debugTable(message, rows) {
    console.groupCollapsed(`[Backtest] ${message}`);
    console.table(rows);
    console.groupEnd();
  }

  debugLog('Инициализация страницы', {
    groupingInputs: GROUP_INPUTS.length,
    checkedGrouping: document.querySelector('input[name="bt-group"]:checked')?.value || null,
  });

  GROUP_INPUTS.forEach(radio => {
    radio.addEventListener('change', event => {
      debugLog('Переключатель группировки изменён', {
        selected: event.target.value,
        checked: document.querySelector('input[name="bt-group"]:checked')?.value || null,
        hasResults: !!currentResults,
      });
      applyGrouping();
    });
  });

  // Entry type locked to confirmation only (market/limit removed)

  // --- Load signals ---
  loadBtn.addEventListener('click', async () => {
    loadBtn.disabled = true;
    loadBtn.textContent = 'Loading...';
    try {
      const period = periodFrom && periodTo && periodFrom.value && periodTo.value
        ? periodFrom.value + ':' + periodTo.value
        : '';
      const sid = sideSel.value.toLowerCase();
      const onlyWorking = document.getElementById('bt-only-working')?.checked ?? true;
      const includeTrading = document.getElementById('bt-source-trading')?.checked ?? false;
      const includeArchive = document.getElementById('bt-source-archive')?.checked ?? true;
      const resp = await fetch(`/api/backtest/signals?limit=500${sid ? '&side=' + sid : ''}${period ? '&period=' + period : ''}&only_working_level=${onlyWorking}&include_trading=${includeTrading}&include_archive=${includeArchive}`);
      const data = await resp.json();
      let items = data.items || [];
      // заполняем чекбоксы из загруженных сигналов (только из архива)
      const symbolsFromSignals = [...new Set(items.map(s => s.symbol))].sort();
      const prevChecked = new Set(getCheckedSymbols());
      renderChecklistFromSymbols(symbolsFromSignals, prevChecked);
      // фильтруем по отмеченным чекбоксам
      const checked = getCheckedSymbols();
      if (checked.length) {
        const set = new Set(checked);
        items = items.filter(s => set.has(s.symbol));
      }
      allSignals = items;
      signalsCount.textContent = `Загружено ${allSignals.length} сигналов${checked.length ? ' (фильтр по символам)' : ''}. Отметьте нужные ниже.`;
      renderSignals();
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
      tr.innerHTML = `<td><input type="checkbox" data-idx="${i}"></td>
        <td>${s.symbol}</td>
        <td class="${s.side === 'LONG' ? 'pos' : 'neg'}">${s.side}</td>
        <td>${s.entry_price}</td>
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
    const selectedIds = getSelectedSignalIds();
    const hasSignals = selectedIds.length > 0;
    const hasVariant = variantRows.some(r => r._selected);
    runBtn.disabled = !(hasSignals && hasVariant);
    const nMethods = document.querySelectorAll('.bt-conf-method:checked').length || 1;
    const nVariants = variantRows.filter(r => r._selected).length;
    const nSignals = selectedIds.length;
    const apiCalls = nSignals * nMethods;
    const estMin = (apiCalls * 1.5 / 60).toFixed(0);
    signalsCount.textContent = `Загружено ${allSignals.length} · выбрано ${nSignals} · вариантов ${nVariants} · методов ${nMethods} · запросов ${apiCalls} ≈${estMin} мин`;
  }

  // --- Symbol checklist: populated from loaded signals only ---
  function getCheckedSymbols() {
    if (!symbolChecklist) return [];
    return Array.from(symbolChecklist.querySelectorAll('input[type="checkbox"]:checked')).map(cb => cb.value);
  }
  function updateChecklistCount() {
    if (symbolChecklistCount) symbolChecklistCount.textContent = `${getCheckedSymbols().length} выбрано`;
  }
  function renderChecklistFromSymbols(symbols, prevChecked) {
    if (!symbolChecklist) return;
    const q = (symbolSearch ? symbolSearch.value : '').trim().toUpperCase();
    const filtered = q ? symbols.filter(s => s.includes(q)) : symbols;
    symbolChecklist.innerHTML = '';
    filtered.forEach(sym => {
      const label = document.createElement('label');
      label.style.cssText = 'display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer;';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.value = sym;
      cb.checked = prevChecked.has(sym);
      label.appendChild(cb);
      label.appendChild(document.createTextNode(' ' + sym));
      symbolChecklist.appendChild(label);
    });
    updateChecklistCount();
  }
  if (symbolSearch) symbolSearch.addEventListener('input', () => {
    // при вводе текста — перерисовываем чекбоксы из текущего списка, сохраняя отмеченные
    const prevChecked = new Set(getCheckedSymbols());
    const symbols = Array.from(symbolChecklist.querySelectorAll('label')).map(l => l.querySelector('input').value);
    // но символовки берём из allSignals
    const allSyms = [...new Set(allSignals.map(s => s.symbol))].sort();
    renderChecklistFromSymbols(allSyms, prevChecked);
  });
  if (symbolClear) symbolClear.addEventListener('click', (e) => {
    e.preventDefault();
    symbolChecklist.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
    updateChecklistCount();
  });

  // --- Variants: helpers ---
  function toRow(v, selected) {
    let tp_type = 'none';
    let tp_value = '';
    if (v.tp_rr != null && v.tp_rr !== '') { tp_type = 'rr'; tp_value = String(v.tp_rr); }
    else if (v.tp_pct != null && v.tp_pct !== '') { tp_type = 'pct'; tp_value = String(v.tp_pct); }
    return {
      id: v.id,
      name: v.name || v.id,
      sl_pct: String(v.sl_pct ?? ''),
      tp_type,
      tp_value,
      trailing_activation_pct: v.trailing_activation_pct != null ? String(v.trailing_activation_pct) : '',
      trailing_stop_pct: v.trailing_stop_pct != null ? String(v.trailing_stop_pct) : '',
      trailing_update_threshold_pct: v.trailing_update_threshold_pct != null ? String(v.trailing_update_threshold_pct) : '',
      trailing_tp_only: !!v.trailing_tp_only,
      breakeven_trigger_pct: v.breakeven_trigger_pct != null ? String(v.breakeven_trigger_pct) : '',
      breakeven_lock_pct: v.breakeven_lock_pct != null ? String(v.breakeven_lock_pct) : '',
      partial_close_pct: v.partial_close_pct != null ? String(v.partial_close_pct) : '',
      partial_close_rr: v.partial_close_rr != null ? String(v.partial_close_rr) : '',
      _selected: !!selected,
    };
  }

  function defaultRow() {
    const n = variantRows.length + 1;
    return {
      id: 'custom_' + Date.now() + '_' + n,
      name: 'Custom ' + n,
      sl_pct: '0.5',
      tp_type: 'rr',
      tp_value: '2',
      trailing_activation_pct: '',
      trailing_stop_pct: '',
      trailing_update_threshold_pct: '',
      trailing_tp_only: false,
      breakeven_trigger_pct: '',
      breakeven_lock_pct: '',
      partial_close_pct: '',
      partial_close_rr: '',
      _selected: true,
    };
  }

  function persistRows() {
    try { localStorage.setItem(LS_KEY, JSON.stringify(variantRows)); } catch (_) {}
  }

  function loadPersisted() {
    try {
      const raw = localStorage.getItem(LS_KEY);
      if (!raw) return null;
      const arr = JSON.parse(raw);
      if (!Array.isArray(arr) || !arr.length) return null;
      return arr;
    } catch (_) { return null; }
  }

  function validateRow(r) {
    const errs = {};
    if (!r.id || !/^[a-zA-Z0-9_-]+$/.test(r.id)) errs.id = true;
    if (!r.name || !r.name.trim()) errs.name = true;
    const sl = parseFloat(r.sl_pct);
    if (!(sl > 0 && sl <= 20)) errs.sl_pct = true;
    if (r.tp_type !== 'none') {
      const tv = parseFloat(r.tp_value);
      if (!(tv > 0 && tv <= 999)) errs.tp_value = true;
    }
    // optional numeric fields: if not empty must be >0
    ['trailing_activation_pct','trailing_stop_pct','trailing_update_threshold_pct','breakeven_trigger_pct','breakeven_lock_pct','partial_close_pct','partial_close_rr'].forEach(k => {
      const v = r[k];
      if (v !== '' && v != null) {
        const n = parseFloat(v);
        if (!(n >= 0 && n <= 100)) errs[k] = true;
      }
    });
    // duplicate id
    const dup = variantRows.filter(x => x.id === r.id).length > 1;
    if (dup) errs.id = true;
    return errs;
  }

  function renderVariantRows() {
    variantsBody.innerHTML = '';
    if (variantRows.length === 0) {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td colspan="15" class="variant-empty">Нет строк — нажмите «+ Добавить строку» в последней строке таблицы</td>`;
      variantsBody.appendChild(tr);
    } else {
      variantRows.forEach((r, idx) => {
        const errs = validateRow(r);
        const tr = document.createElement('tr');
        tr.dataset.idx = String(idx);
        tr.innerHTML = `
          <td class="col-check"><input type="checkbox" data-field="_selected" ${r._selected ? 'checked' : ''}></td>
          <td class="col-id"><input type="text" data-field="id" value="${esc(r.id)}" class="${errs.id ? 'invalid' : ''}" placeholder="id"></td>
          <td class="col-name"><input type="text" data-field="name" value="${esc(r.name)}" class="${errs.name ? 'invalid' : ''}" placeholder="Имя"></td>
          <td class="col-num"><input type="number" step="0.1" min="0.05" max="20" data-field="sl_pct" value="${esc(r.sl_pct)}" class="${errs.sl_pct ? 'invalid' : ''}"></td>
          <td class="col-tp-type"><select data-field="tp_type" class="${errs.tp_type ? 'invalid' : ''}"><option value="rr" ${r.tp_type==='rr'?'selected':''}>RR</option><option value="pct" ${r.tp_type==='pct'?'selected':''}>%</option><option value="none" ${r.tp_type==='none'?'selected':''}>—</option></select></td>
          <td class="col-tp-val"><input type="number" step="0.1" min="0.1" max="999" data-field="tp_value" value="${esc(r.tp_value)}" class="${errs.tp_value ? 'invalid' : ''}" ${r.tp_type==='none'?'disabled':''} placeholder="${r.tp_type==='rr'?'2':r.tp_type==='pct'?'1':''}"></td>
          <td class="col-num"><input type="number" step="0.1" min="0" max="100" data-field="trailing_activation_pct" value="${esc(r.trailing_activation_pct)}" class="${errs.trailing_activation_pct ? 'invalid' : ''}" placeholder="—"></td>
          <td class="col-num"><input type="number" step="0.1" min="0" max="100" data-field="trailing_stop_pct" value="${esc(r.trailing_stop_pct)}" class="${errs.trailing_stop_pct ? 'invalid' : ''}" placeholder="—"></td>
          <td class="col-num"><input type="number" step="0.1" min="0" max="100" data-field="trailing_update_threshold_pct" value="${esc(r.trailing_update_threshold_pct)}" class="${errs.trailing_update_threshold_pct ? 'invalid' : ''}" placeholder="—"></td>
          <td class="col-check-sm"><input type="checkbox" data-field="trailing_tp_only" ${r.trailing_tp_only ? 'checked' : ''}></td>
          <td class="col-num"><input type="number" step="0.1" min="0" max="100" data-field="breakeven_trigger_pct" value="${esc(r.breakeven_trigger_pct)}" class="${errs.breakeven_trigger_pct ? 'invalid' : ''}" placeholder="—"></td>
          <td class="col-num"><input type="number" step="0.1" min="0" max="100" data-field="breakeven_lock_pct" value="${esc(r.breakeven_lock_pct)}" class="${errs.breakeven_lock_pct ? 'invalid' : ''}" placeholder="—"></td>
          <td class="col-num"><input type="number" step="1" min="0" max="100" data-field="partial_close_pct" value="${esc(r.partial_close_pct)}" class="${errs.partial_close_pct ? 'invalid' : ''}" placeholder="—"></td>
          <td class="col-num"><input type="number" step="0.1" min="0" max="100" data-field="partial_close_rr" value="${esc(r.partial_close_rr)}" class="${errs.partial_close_rr ? 'invalid' : ''}" placeholder="—"></td>
          <td style="text-align:center;"><button type="button" class="btn-del" data-action="delete" title="Удалить строку">×</button></td>
        `;
        variantsBody.appendChild(tr);
      });
    }
    const selCount = variantRows.filter(r => r._selected).length;
    variantCount.textContent = `${variantRows.length} строк, выбрано ${selCount}`;
    updateRunBtn();
    // header checkbox state
    if (variantRows.length === 0) { varSelAllCb.checked = false; varSelAllCb.indeterminate = false; }
    else if (selCount === variantRows.length) { varSelAllCb.checked = true; varSelAllCb.indeterminate = false; }
    else if (selCount === 0) { varSelAllCb.checked = false; varSelAllCb.indeterminate = false; }
    else { varSelAllCb.checked = false; varSelAllCb.indeterminate = true; }
    updateRunBtn();
  }

  function esc(s) { return String(s ?? '').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;'); }

  // Delegated editing
  variantsBody.addEventListener('input', (e) => {
    const field = e.target.dataset.field;
    if (!field) return;
    const tr = e.target.closest('tr');
    if (!tr) return;
    const idx = parseInt(tr.dataset.idx);
    if (Number.isNaN(idx) || !variantRows[idx]) return;
    const val = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    variantRows[idx][field] = val;
    // tp_type change enables/disables tp_value
    if (field === 'tp_type') {
      // keep tp_value but toggle disabled in DOM on next render; also update immediately
      persistRows();
      renderVariantRows();
      return;
    }
    persistRows();
    // live validation class
    const errs = validateRow(variantRows[idx]);
    e.target.classList.toggle('invalid', !!errs[field]);
    updateRunBtn();
    variantCount.textContent = `${variantRows.length} строк, выбрано ${variantRows.filter(r=>r._selected).length}`;
  });

  variantsBody.addEventListener('change', (e) => {
    const field = e.target.dataset.field;
    if (!field) return;
    const tr = e.target.closest('tr');
    if (!tr) return;
    const idx = parseInt(tr.dataset.idx);
    if (Number.isNaN(idx) || !variantRows[idx]) return;
    const val = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    variantRows[idx][field] = val;
    persistRows();
    if (field === '_selected' || field === 'trailing_tp_only') {
      variantCount.textContent = `${variantRows.length} строк, выбрано ${variantRows.filter(r=>r._selected).length}`;
      updateRunBtn();
      const selCount = variantRows.filter(r=>r._selected).length;
      if (selCount === variantRows.length) { varSelAllCb.checked = true; varSelAllCb.indeterminate = false; }
      else if (selCount === 0) { varSelAllCb.checked = false; varSelAllCb.indeterminate = false; }
      else { varSelAllCb.indeterminate = true; }
    }
    // re-validate border
    const errs = validateRow(variantRows[idx]);
    if (errs[field]) e.target.classList.add('invalid'); else e.target.classList.remove('invalid');
  });

  variantsBody.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-action="delete"]');
    if (!btn) return;
    const tr = btn.closest('tr');
    const idx = parseInt(tr.dataset.idx);
    if (Number.isNaN(idx)) return;
    variantRows.splice(idx, 1);
    persistRows();
    renderVariantRows();
  });

  varSelAllCb.addEventListener('change', () => {
    const checked = varSelAllCb.checked;
    variantRows.forEach(r => r._selected = checked);
    persistRows();
    renderVariantRows();
  });

  addRowBtn.addEventListener('click', () => {
    variantRows.push(defaultRow());
    persistRows();
    renderVariantRows();
    // focus last id input
    const last = variantsBody.querySelector('tr:last-child input[data-field="id"]');
    if (last) last.focus();
  });

  resetBtn.addEventListener('click', () => {
    if (!confirm('Сбросить таблицу к заводским вариантам? Текущие правки будут потеряны.')) return;
    localStorage.removeItem(LS_KEY);
    loadVariants(true);
  });

  // --- Save / Load Settings ---
  document.getElementById('bt-save-settings').addEventListener('click', async () => {
    const settings = {
      period_from: document.getElementById('bt-period-from').value,
      side: document.getElementById('bt-side').value,
      only_working: document.getElementById('bt-only-working').checked,
      source_archive: document.getElementById('bt-source-archive').checked,
      source_trading: document.getElementById('bt-source-trading').checked,
      conf_methods: Array.from(document.querySelectorAll('.bt-conf-method:checked')).map(cb => parseInt(cb.value)),
      lookback: parseInt(document.getElementById('bt-lookback').value),
      lookforward: parseInt(document.getElementById('bt-lookforward').value),
      variants: variantRows,
      selected_symbols: getCheckedSymbols(),
      group_mode: document.querySelector('input[name="bt-group"]:checked')?.value || 'variant',
    };
    const now = new Date();
    const pad = n => String(n).padStart(2, '0');
    const defaultName = `backtest_${now.getFullYear()}-${pad(now.getMonth()+1)}-${pad(now.getDate())}_${pad(now.getHours())}-${pad(now.getMinutes())}.json`;
    try {
      const handle = await window.showSaveFilePicker({
        suggestedName: defaultName,
        types: [{ description: 'JSON', accept: { 'application/json': ['.json'] } }],
      });
      const writable = await handle.createWritable();
      await writable.write(JSON.stringify(settings, null, 2));
      await writable.close();
    } catch (e) {
      if (e.name !== 'AbortError') alert('Ошибка сохранения: ' + e.message);
    }
  });

  document.getElementById('bt-load-settings').addEventListener('click', () => {
    document.getElementById('bt-load-file').click();
  });

  document.getElementById('bt-load-file').addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      try {
        const s = JSON.parse(ev.target.result);
        if (s.period_from) document.getElementById('bt-period-from').value = s.period_from;
        if (s.side) document.getElementById('bt-side').value = s.side;
        if (s.only_working !== undefined) document.getElementById('bt-only-working').checked = s.only_working;
        if (s.source_archive !== undefined) document.getElementById('bt-source-archive').checked = s.source_archive;
        if (s.source_trading !== undefined) document.getElementById('bt-source-trading').checked = s.source_trading;
        if (s.lookback) document.getElementById('bt-lookback').value = s.lookback;
        if (s.lookforward) document.getElementById('bt-lookforward').value = s.lookforward;
        if (s.conf_methods) {
          document.querySelectorAll('.bt-conf-method').forEach(cb => {
            cb.checked = s.conf_methods.includes(parseInt(cb.value));
          });
        }
        if (s.variants) {
          variantRows = s.variants;
          persistRows();
          renderVariantRows();
        }
        if (s.selected_symbols) {
          const set = new Set(s.selected_symbols);
          const allSyms = [...new Set(allSignals.map(sig => sig.symbol))].sort();
          renderChecklistFromSymbols(allSyms.length ? allSyms : s.selected_symbols, set);
        }
        if (s.group_mode) {
          const radio = document.querySelector(`input[name="bt-group"][value="${s.group_mode}"]`);
          if (radio) radio.checked = true;
        }
      } catch (err) {
        alert('Ошибка загрузки файла: ' + err.message);
      }
    };
    reader.readAsText(file);
    e.target.value = '';
  });

  // --- Load variants ---
  async function loadVariants(forceBuiltin) {
    try {
      const resp = await fetch('/api/backtest/variants');
      const data = await resp.json();
      const builtin = data.items || [];
      if (!forceBuiltin) {
        const saved = loadPersisted();
        if (saved) {
          variantRows = saved;
          // migrate old shape if needed: ensure all fields exist
          variantRows.forEach(r => {
            if (r.tp_type === undefined) { const tmp = toRow(r, r._selected); Object.assign(r, tmp); }
            if (r._selected === undefined) r._selected = true;
          });
          renderVariantRows();
          return;
        }
      }
      variantRows = builtin.map((v, i) => toRow(v, i < 3));
      persistRows();
      renderVariantRows();
    } catch (e) { /* ignore */ }
  }

  // --- Run backtest ---
  runBtn.addEventListener('click', startBacktest);
  document.querySelectorAll('.bt-conf-method').forEach(cb => cb.addEventListener('change', updateRunBtn));

  function buildVariantPayloads() {
    const selected = variantRows.filter(r => r._selected);
    const errors = [];
    const payloads = [];
    const seenIds = new Set();
    selected.forEach((r, idx) => {
      const errs = validateRow(r);
      if (Object.keys(errs).length) {
        errors.push(`Строка ${idx+1} (${r.id}): исправьте подсвеченные поля`);
        return;
      }
      if (seenIds.has(r.id)) { errors.push(`Дубликат ID: ${r.id}`); return; }
      seenIds.add(r.id);
      const sl = parseFloat(r.sl_pct);
      let tp_rr = null, tp_pct = null;
      if (r.tp_type === 'rr') tp_rr = String(parseFloat(r.tp_value));
      else if (r.tp_type === 'pct') tp_pct = String(parseFloat(r.tp_value));
      const p = {
        id: r.id.trim(),
        name: r.name.trim() || r.id.trim(),
        sl_pct: String(sl),
        tp_rr: tp_rr,
        tp_pct: tp_pct,
        trailing_activation_pct: r.trailing_activation_pct !== '' ? String(parseFloat(r.trailing_activation_pct)) : null,
        trailing_stop_pct: r.trailing_stop_pct !== '' ? String(parseFloat(r.trailing_stop_pct)) : null,
        trailing_update_threshold_pct: r.trailing_update_threshold_pct !== '' ? String(parseFloat(r.trailing_update_threshold_pct)) : null,
        trailing_tp_only: !!r.trailing_tp_only,
        breakeven_trigger_pct: r.breakeven_trigger_pct !== '' ? String(parseFloat(r.breakeven_trigger_pct)) : null,
        breakeven_lock_pct: r.breakeven_lock_pct !== '' ? String(parseFloat(r.breakeven_lock_pct)) : null,
        partial_close_pct: r.partial_close_pct !== '' ? String(parseFloat(r.partial_close_pct)) : null,
        partial_close_rr: r.partial_close_rr !== '' ? String(parseFloat(r.partial_close_rr)) : null,
      };
      payloads.push(p);
    });
    return { payloads, errors };
  }

  async function startBacktest() {
    const signalIds = getSelectedSignalIds();
    if (signalIds.length === 0) {
      errorDiv.textContent = 'Select at least one signal';
      errorDiv.classList.remove('hidden');
      return;
    }

    const { payloads, errors } = buildVariantPayloads();
    if (errors.length) {
      errorDiv.textContent = errors[0];
      errorDiv.classList.remove('hidden');
      renderVariantRows();
      return;
    }
    if (payloads.length === 0) {
      errorDiv.textContent = 'Select at least one variant (отметьте чекбокс в первом столбце)';
      errorDiv.classList.remove('hidden');
      return;
    }

    const entryType = (document.querySelector('input[name="bt-entry"]:checked') || document.querySelector('input[name="bt-entry"]')).value || 'confirmation';

    runBtn.disabled = true;
    errorDiv.classList.add('hidden');
    progressWrap.classList.remove('hidden');
    resultsWrap.classList.add('hidden');
    progressFill.style.width = '0%';
    progressText.textContent = 'Starting...';

    try {
      const body = {
        signal_ids: signalIds,
        variants: payloads.map(p => p.id),
        custom_variants: payloads,
        entry_type: entryType,
        limit_offset: 0.2,
        confirmation_methods: Array.from(document.querySelectorAll('.bt-conf-method:checked')).map(cb => parseInt(cb.value)),
        lookback: Math.max(0, Math.min(500, parseInt(document.getElementById('bt-lookback').value) || 0)),
        lookforward: Math.max(20, Math.min(2000, parseInt(document.getElementById('bt-lookforward').value) || 1000)),
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
        const detail = Array.isArray(data.detail)
          ? data.detail.map(e => {
              let msg = e.msg || '';
              msg = msg.replace(/^Value error,\s*/, '');
              return msg;
            }).join('; ')
          : (data.detail || 'Failed to start backtest');
        throw new Error(detail);
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
        progressFill.textContent = pct + '%';
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
        progressFill.textContent = '100%';
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
    debugLog('Начало загрузки отчёта', { jobId });
    const resp = await fetch(`/api/backtest/report/${jobId}`);
    debugLog('Ответ отчёта получен', { status: resp.status, ok: resp.ok });
    if (!resp.ok) throw new Error(`Не удалось загрузить отчёт: HTTP ${resp.status}`);
    currentResults = await resp.json();
    debugLog('Отчёт загружен', {
      tradesCount: currentResults.trades?.length,
      metricsCount: currentResults.metrics?.length,
      hasTrades: !!currentResults.trades,
      hasMetrics: !!currentResults.metrics,
      symbols: [...new Set((currentResults.trades || []).map(t => t.symbol))].length,
    });
    debugTable('Первые сделки из отчёта', (currentResults.trades || []).slice(0, 10).map(t => ({
      symbol: t.symbol,
      variant: t.variant_id,
      method: t.confirmation_method,
      pnl: t.pnl_pct,
    })));
    resultsWrap.classList.remove('hidden');
    applyGrouping();
    renderTrades(currentResults.trades);
  }

  function applyGrouping() {
    try {
      const selected = document.querySelector('input[name="bt-group"]:checked');
      if (!selected) {
        console.error('[Backtest] Не найден выбранный переключатель группировки');
        return;
      }
      if (!currentResults) {
        debugLog('Группировка пропущена: отчёт ещё не загружен');
        return;
      }
      const mode = selected.value;
      const trades = currentResults.trades || [];
      debugLog('Пересчёт группировки', { mode, trades: trades.length });
      currentBestBySymbol = computeMetricsBySymbol(trades);
      let metrics;
      if (mode === 'symbol') {
        debugLog('Расчёт по символам: выбираю лучшую комбинацию для каждого символа');
        metrics = currentBestBySymbol;
        debugLog('Расчёт по символам завершён', { rows: metrics.length });
      } else {
        metrics = currentResults.metrics || [];
        debugLog('Используются серверные метрики по комбинациям', { rows: metrics.length });
      }
      debugTable('Метрики для основной таблицы', metrics.map(m => ({
        group: m.variant_name,
        trades: m.total_trades,
        winrate: m.winrate,
        pnl: m.total_pnl_pct,
      })));
      renderMetrics(metrics);
      renderEquity(metrics);
      renderPnlBar(metrics);
    } catch (error) {
      console.error('[Backtest] Ошибка переключения группировки', error);
      if (errorDiv) {
        errorDiv.textContent = `Ошибка группировки: ${error.message}`;
        errorDiv.classList.remove('hidden');
      }
    }
  }

  function computeMetricsBySymbol(trades) {
    // Group by symbol
    const bySymbol = {};
    trades.forEach(t => {
      if (!bySymbol[t.symbol]) bySymbol[t.symbol] = [];
      bySymbol[t.symbol].push(t);
    });
    debugLog('Символы для группировки', {
      count: Object.keys(bySymbol).length,
      symbols: Object.keys(bySymbol),
    });

    // For each symbol, find best variant+method by total PnL %
    const result = [];
    Object.entries(bySymbol).forEach(([symbol, symTrades]) => {
      const byVariant = {};
      symTrades.forEach(t => {
        const key = `${t.variant_id}|${t.confirmation_method}`;
        if (!byVariant[key]) byVariant[key] = { variant_id: t.variant_id, variant_name: t.variant_name, method: t.confirmation_method, trades: [] };
        byVariant[key].trades.push(t);
      });
      let best = null;
      let bestPnl = -Infinity;
      Object.values(byVariant).forEach(g => {
        const m = computeMetricsLocal(g.variant_id, `${g.variant_name}`, g.trades);
        debugLog('Кандидат комбинации по символу', {
          symbol,
          combination: `${g.variant_id}|${g.method}`,
          trades: m.total_trades,
          pnl: m.total_pnl_pct,
          winrate: m.winrate,
        });
        if (m.total_pnl_pct > bestPnl) {
          bestPnl = m.total_pnl_pct;
          best = m;
          best.symbol = symbol;
          best.method = g.method;
        }
      });
      if (best) {
        best.combination = best.variant_name;
        best.variant_name = `${symbol} | ${best.variant_name}`;
        debugLog('Выбрана лучшая комбинация по символу', {
          symbol,
          combination: best.combination,
          method: best.method,
          trades: best.total_trades,
          pnl: best.total_pnl_pct,
        });
        result.push(best);
      }
    });
    result.sort((a, b) => b.total_pnl_pct - a.total_pnl_pct);
    debugTable('Итоговые строки группировки по символам', result.map(m => ({
      symbol: m.symbol,
      combination: m.combination,
      trades: m.total_trades,
      pnl: m.total_pnl_pct,
    })));
    return result;
  }

  function computeMetricsLocal(variantId, variantName, trades) {
    const m = { variant_id: variantId, variant_name: variantName };
    if (!trades.length) return m;
    m.total_trades = trades.length;
    let wins = 0, losses = 0, noEntry = 0, totalPnlPct = 0, winPctSum = 0, lossPnlPctSum = 0;
    let maxDd = 0, peak = 0, equity = 0;
    trades.forEach(t => {
      if (t.exit_reason === 'no_entry') { noEntry++; return; }
      const pct = t.pnl_pct || 0;
      totalPnlPct += pct;
      equity += pct;
      if (equity > peak) peak = equity;
      const dd = peak - equity;
      if (dd > maxDd) maxDd = dd;
      if (pct > 0) { wins++; winPctSum += pct; }
      else if (pct < 0) { losses++; lossPnlPctSum += pct; }
    });
    m.wins = wins;
    m.losses = losses;
    m.no_entry = noEntry;
    m.winrate = trades.length - noEntry > 0 ? wins / (trades.length - noEntry) * 100 : 0;
    m.total_pnl_pct = totalPnlPct;
    m.avg_pnl_pct = trades.length - noEntry > 0 ? totalPnlPct / (trades.length - noEntry) : 0;
    m.avg_win_pct = winPctSum / wins || 0;
    m.avg_loss_pct = lossPnlPctSum / losses || 0;
    const absLosses = Math.abs(lossPnlPctSum);
    m.profit_factor = absLosses > 0 ? (winPctSum / absLosses) : Infinity;
    const wr = m.winrate / 100;
    m.expectancy_pct = wr * m.avg_win_pct + (1 - wr) * m.avg_loss_pct;
    m.max_drawdown = maxDd;
    m.long_trades = trades.filter(t => t.side === 'LONG' && t.exit_reason !== 'no_entry').length;
    m.short_trades = trades.filter(t => t.side === 'SHORT' && t.exit_reason !== 'no_entry').length;
    const longWins = trades.filter(t => t.side === 'LONG' && t.pnl_pct > 0).length;
    const shortWins = trades.filter(t => t.side === 'SHORT' && t.pnl_pct > 0).length;
    m.long_winrate = m.long_trades ? longWins / m.long_trades * 100 : 0;
    m.short_winrate = m.short_trades ? shortWins / m.short_trades * 100 : 0;
    m.equity_curve_pct = [0];
    let eq = 0;
    trades.forEach(t => {
      if (t.exit_reason === 'no_entry') { m.equity_curve_pct.push(eq); return; }
      eq += (t.pnl_pct || 0);
      m.equity_curve_pct.push(eq);
    });
    return m;
  }

  // --- Render metrics ---
  let _metricsSortKey = 'total_pnl_pct';
  let _metricsSortDir = -1; // -1 = desc, 1 = asc, 0 = disabled (original order)
  let _metricsOriginalOrder = [];

  function renderMetrics(metrics) {
    currentMetrics = metrics || [];
    _metricsOriginalOrder = [...currentMetrics]; // preserve original order for "disabled" state
    debugLog('Отрисовка метрик', {
      rows: currentMetrics.length,
      grouping: document.querySelector('input[name="bt-group"]:checked')?.value || null,
    });
    _metricsSortKey = 'total_pnl_pct';
    _metricsSortDir = -1;
    renderMetricsTable();
    renderSideStats(currentMetrics);

    const table = document.getElementById('bt-metrics-table');
    table.querySelectorAll('th[data-sort]').forEach(th => {
      th.addEventListener('click', () => {
        const key = th.dataset.sort;
        if (_metricsSortKey === key) {
          // Cycle through: desc (-1) → asc (1) → disabled (0) → desc (-1)
          if (_metricsSortDir === -1) {
            _metricsSortDir = 1;
          } else if (_metricsSortDir === 1) {
            _metricsSortDir = 0;
          } else {
            _metricsSortDir = -1;
          }
        } else {
          _metricsSortKey = key;
          _metricsSortDir = (key === 'variant_name' || key === 'avg_loss_pct') ? 1 : -1;
        }
        table.querySelectorAll('th[data-sort]').forEach(h => h.classList.remove('sort-asc', 'sort-desc', 'sort-off'));
        if (_metricsSortDir === 1) th.classList.add('sort-asc');
        else if (_metricsSortDir === -1) th.classList.add('sort-desc');
        else th.classList.add('sort-off');
        renderMetricsTable();
      });
    });
    // highlight default sort column
    const defaultTh = table.querySelector(`th[data-sort="${_metricsSortKey}"]`);
    if (defaultTh) defaultTh.classList.add('sort-desc');
  }

  function renderMetricsTable() {
    const tbody = document.getElementById('bt-metrics-body');
    tbody.innerHTML = '';
    let sorted;
    if (_metricsSortDir === 0) {
      // Disabled - use original order
      sorted = _metricsOriginalOrder;
    } else {
      sorted = [...currentMetrics].sort((a, b) => {
        let va = a[_metricsSortKey], vb = b[_metricsSortKey];
        if (typeof va === 'string') return _metricsSortDir * va.localeCompare(vb);
        return _metricsSortDir * ((va || 0) - (vb || 0));
      });
    }
    const bestPnl = Math.max(...sorted.map(m => m.total_pnl_pct));
    sorted.forEach(m => {
      const tr = document.createElement('tr');
      if (m.total_pnl_pct === bestPnl) tr.className = 'best';
      tr.innerHTML = `<td>${m.variant_name}</td>
        <td>${m.total_trades}</td>
        <td style="color:#888;">${m.no_entry || 0}</td>
        <td class="${m.winrate >= 50 ? 'pos' : 'neg'}">${m.winrate.toFixed(2)}%</td>
        <td class="${m.total_pnl_pct >= 0 ? 'pos' : 'neg'}">${m.total_pnl_pct >= 0 ? '+' : ''}${m.total_pnl_pct.toFixed(2)}%</td>
        <td>${m.profit_factor === 'Infinity' ? '∞' : m.profit_factor}</td>
        <td class="neg">-${m.max_drawdown_pct ? m.max_drawdown_pct.toFixed(2) : m.max_drawdown.toFixed(2)}</td>
        <td class="pos">+${m.avg_win_pct.toFixed(2)}%</td>
        <td class="neg">${m.avg_loss_pct.toFixed(2)}%</td>
        <td class="${m.expectancy_pct >= 0 ? 'pos' : 'neg'}">${m.expectancy_pct >= 0 ? '+' : ''}${m.expectancy_pct.toFixed(2)}%</td>`;
      tbody.appendChild(tr);
    });
  }

  function renderSideStats(metrics) {
    const sideStats = document.getElementById('bt-side-stats');
    sideStats.innerHTML = '';
    metrics.forEach(m => {
      const div = document.createElement('div');
      div.innerHTML = `<div style="background:#16213e;padding:8px 12px;border-radius:6px;border-left:3px solid #2196F3;">
        <div style="font-size:11px;color:#888;">${m.variant_name}</div>
        <div>LONG: <span class="${m.long_winrate >= 50 ? 'pos' : 'neg'}">${m.long_winrate.toFixed(2)}%</span> (${m.long_trades} trades)</div>
        <div>SHORT: <span class="${m.short_winrate >= 50 ? 'pos' : 'neg'}">${m.short_winrate.toFixed(2)}%</span> (${m.short_trades} trades)</div>
      </div>`;
      sideStats.appendChild(div);
    });

    const symbolBody = document.getElementById('bt-symbol-body');
    symbolBody.innerHTML = '';
    if (currentResults?.trades) {
      // The footer always shows one best variant+method per symbol.
      const bySymbol = {};
      currentBestBySymbol.forEach(m => {
        bySymbol[m.symbol] = {
          wins: m.wins,
          losses: m.losses,
          pnl_pct: m.total_pnl_pct,
          variant: m.combination,
        };
      });
      debugTable('Подвал: лучшая комбинация по каждому символу', currentBestBySymbol.map(m => ({
        symbol: m.symbol,
        combination: m.combination,
        trades: m.total_trades,
        pnl: m.total_pnl_pct,
      })));
      Object.keys(bySymbol).sort((a, b) => bySymbol[b].pnl_pct - bySymbol[a].pnl_pct).forEach(sym => {
        const d = bySymbol[sym];
        const total = d.wins + d.losses;
        const wr = total > 0 ? (d.wins / total) * 100 : 0;
        const variantInfo = d.variant ? ' <span style="color:#888;font-size:10px;">(' + d.variant + ')</span>' : '';
        const tr = document.createElement('tr');
        tr.innerHTML = '<td>' + sym + variantInfo + '</td><td>' + total + '</td>' +
          '<td class="' + (wr >= 50 ? 'pos' : 'neg') + '">' + wr.toFixed(2) + '%</td>' +
          '<td class="' + (d.pnl_pct >= 0 ? 'pos' : 'neg') + '">' + (d.pnl_pct >= 0 ? '+' : '') + d.pnl_pct.toFixed(2) + '%</td>';
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
      const data = m.equity_curve_pct || m.equity_curve || [];
      return {
        label: m.variant_name,
        data: data.map((v, idx) => ({ x: idx, y: v })),
        borderColor: `hsl(${hue}, 70%, 50%)`,
        borderWidth: 1.5,
        pointRadius: 0,
        pointHoverRadius: 4,
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
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: {
            type: 'linear',
            title: { display: true, text: 'Trade #', color: '#888' },
            ticks: { color: '#888', stepSize: 1 },
            grid: { color: '#222' },
          },
          y: {
            title: { display: true, text: 'Equity %', color: '#888' },
            ticks: { color: '#888', callback: v => v.toFixed(1) + '%' },
            grid: { color: '#222' },
          },
        },
        plugins: {
          legend: { labels: { color: '#ccc', font: { size: 11 } } },
          tooltip: {
            callbacks: {
              title: items => items.length ? `Trade #${items[0].parsed.x}` : '',
              label: item => `${item.dataset.label}: ${item.parsed.y >= 0 ? '+' : ''}${item.parsed.y.toFixed(2)}%`,
            },
          },
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
          label: 'Total PnL %',
          data: metrics.map(m => m.total_pnl_pct),
          backgroundColor: metrics.map(m => m.total_pnl_pct >= 0 ? '#4CAF50' : '#f44336'),
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        indexAxis: 'y',
        scales: {
          x: { ticks: { color: '#888', callback: v => v + '%' }, grid: { color: '#222' } },
          y: { ticks: { color: '#ccc', font: { size: 11 }, autoSkip: false }, grid: { display: false } },
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
    document.getElementById('bt-f-method').addEventListener('change', () => filterTrades(trades));
  }

  function populateTradeFilters(trades) {
    const symbols = [...new Set(trades.map(t => t.symbol))].sort();
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
    const method = document.getElementById('bt-f-method').value;

    let filtered = allTrades;
    if (sym) filtered = filtered.filter(t => t.symbol === sym);
    if (side) filtered = filtered.filter(t => t.side === side);
    if (result === 'win') filtered = filtered.filter(t => t.pnl_pct > 0);
    if (result === 'loss') filtered = filtered.filter(t => t.pnl_pct < 0);
    if (result === 'no_entry') filtered = filtered.filter(t => t.exit_reason === 'no_entry');
    if (varId) filtered = filtered.filter(t => t.variant_id === varId);
    if (method !== '') filtered = filtered.filter(t => String(t.confirmation_method) === method);
    displayTrades(filtered);
  }

  function openReplayForTrade(trade) {
  const params = new URLSearchParams({
    symbol: trade.symbol,
    display_from: trade.entry_time ? trade.entry_time.split('T')[0] : '',
    side: trade.side,
    entry_price: trade.entry_price,
    stop_price: trade.stop_price,
    take_price: trade.take_price,
    variant_id: trade.variant_id,
    variant_name: trade.variant_name,
    confirmation_method: trade.confirmation_method,
    trailing_stop_pct: trade.trailing_stop_pct || '',
    trailing_activation_pct: trade.trailing_activation_pct || '',
    trailing_update_threshold_pct: trade.trailing_update_threshold_pct || '',
    trailing_tp_only: trade.trailing_tp_only || false,
    breakeven_trigger_pct: trade.breakeven_trigger_pct || '',
    breakeven_lock_pct: trade.breakeven_lock_pct || '',
    partial_close_pct: trade.partial_close_pct || '',
    partial_close_rr: trade.partial_close_rr || '',
  });
  window.open(`/?${params}`, '_blank');
}

function displayTrades(trades) {
    const tbody = document.getElementById('bt-trades-body');
    tbody.innerHTML = '';
    trades.forEach((t, i) => {
      const methodLabels = {0: 'Touch', 1: '1 bar', 2: '2 bars'};
      const methodLabel = methodLabels[t.confirmation_method] || '-';
      const tr = document.createElement('tr');
      const isNoEntry = t.exit_reason === 'no_entry';
      tr.style.cursor = 'pointer';
      tr.title = 'Double-click to open replay with this trade';
      tr.addEventListener('dblclick', () => openReplayForTrade(t));
      tr.innerHTML = `<td>${i + 1}</td>
        <td>${t.symbol}</td>
        <td class="${isNoEntry ? '' : (t.side === 'LONG' ? 'pos' : 'neg')}">${t.side}</td>
        <td>${t.entry_price}</td>
        <td>${isNoEntry ? '-' : (t.exit_price || '-')}</td>
        <td class="${isNoEntry ? '' : (t.pnl_pct >= 0 ? 'pos' : 'neg')}">${isNoEntry ? '0.00%' : (t.pnl_pct >= 0 ? '+' : '') + t.pnl_pct.toFixed(2) + '%'}</td>
        <td>${isNoEntry ? '-' : t.bars_held}</td>
        <td>${isNoEntry ? 'no entry' : (t.exit_reason || '-')}</td>
        <td style="font-size:11px;color:#888;">${methodLabel}</td>
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
  loadVariants(false);
  fetch('/api/version').then(r => r.json()).then(d => {
    const el = document.getElementById('bt-version');
    if (el) el.textContent = 'v' + d.version;
  }).catch(() => {});
})();
