# Changelog

## v1.3.2 (2026-09-21)

### Fixed
- **TREEUSDT price display precision** (`src/level_tester/api/app.py`, `web/hourbounce.js`): HourBounce now derives display precision from `tick_size` (5 for TREEUSDT) instead of exchange `price_precision` (7). Previously the chart y-axis showed spurious `0.0000006` labels instead of `0.0476`. Added batch price spec lookup (`_hb_price_specs`) for signal list — single catalog query instead of N sessions. Frontend `calcFitRange` skips PGv2 zero placeholders (`"0.0000000"`), and race guards (`reviewSeq`/`matrixSeq`/`signalsSeq`) prevent stale responses from overwriting the chart.

### Added
- **Shared-DB-first candle loading** (`src/level_tester/backtester/candle_loader.py`): `CandleLoader` now reads from the shared `candles` table via `application.candle_service.get_candles` when a session factory is available (via explicit arg or `DATABASE_URL`), falling back to direct Binance API on any DB failure.
- **HourBounce frontend race guards** (`web/hourbounce.js`): `reviewSeq`, `matrixSeq`, `signalsSeq` sequence counters invalidate stale async responses when signal/tab/mode/execMode changes. Metadata (`price_precision`, `tick_size`) from API responses now propagates to `state.sel` for correct price formatting.

### Changed
- **HourBounce price spec logic** (`src/level_tester/api/app.py`): `_hb_price_spec` and new `_hb_price_specs` use `price_precision_from_tick(tick_size)` for display precision, matching replay engine behavior. Fallback derives precision from entry price decimal digits.

### Tests
- Added 3 regression tests in `tests/test_api.py`:
  - `test_hb_price_spec_uses_tick_size_not_exchange_precision` — TREEUSDT case
  - `test_hb_price_spec_falls_back_to_price_digits` — unknown symbol fallback
  - `test_hb_price_specs_batch_one_query_and_fallback` — batch query + fallback

## v1.3.1 (2026-09-18)

### Added
- **Сортировка списка сигналов** (`src/level_tester/api/app.py`, `web/hourbounce.html`, `web/hourbounce.js`): добавлены поля `created_ts` / `worked_ts` и новые режимы сортировки `created_desc/asc` (по дате создания), `worked_desc/asc` (по дате касания/отработки), без даты — всегда в конец. Выпадающий список `f-sort` обновлён.

### Added
- **Сортировка строк PnL-отчёта** (`src/level_tester/api/app.py`, `web/report.html`): те же режимы сортировки (`created`, `worked`, `name`, `symbol`, `date`) в отчёте, применяются до группировки, выбор сохраняется в localStorage, CSV выгружается в отсортированном порядке.

### Fixed
- **Защита матрицы от ломанных ячеек** (`web/hourbounce.js`): при отсутствии ячейки `T1M/SL*` (версии фронта/бэка расходятся) — предупреждение в консоль и пропуск карточки вместо падения всего рендера.

## v1.3.0 (2026-09-18)

### Fixed
- **Ложные NO_ENTRY у T1M в отчётах** (`src/level_tester/infrastructure/hourbounce_store.py`, `src/level_tester/api/app.py`): две compounded-причины — (1) догрузка дыр M1 из Binance шла без ретраев и под rate-limit массовых прогонов молча оставляла дыры в кеше; добавлены 3 попытки с backoff + warning при неустранимой дыре; (2) валидность `hb_reviews` привязана только к M5-окну, поэтому T1M, однажды посчитанный на дырявых M1, отдавался из кеша вечно — теперь ячейки T1M всегда пересчитываются свежо поверх кеша (T2/T3 по-прежнему из кеша). Проверено на `ZECUSDT closed_sl`: было `NO_ENTRY`, стало `STOP` без `refresh`
- **Отчёт без фильтра дат показывал только сентябрь** (`src/level_tester/api/app.py`, `web/report.html`): `f-limit=100` резал 100 свежих до клиентского фильтра «только отторгованные» — старые traded не попадали никогда; добавлен серверный `traded_only` в `/api/hourbounce/signals` (применяется до лимита и сортировки среза), чекбокс шлёт его на сервер; потолки подняты (`reader.read` 500→5000, API `le=5000`, `HbReportRequest` до 5000, в UI добавлены 1000/«все»). Проверка: `traded_only` возвращает все 102 отторгованные за 07.06–15.09 вместо 14 за 07–15.09

### Changed
- **Матрица T1 → T1M** (`src/level_tester/backtester/hourbounce.py`, `src/level_tester/api/app.py`, `web/hourbounce.html`, `web/hourbounce.js`, `scripts/validate_vs_archive.py`): ячейки касания в матрице 9 шт теперь считаются маркетом на касании по M1 (вход по принту = level, при гэпе — по open минуты, поиск и исполнение полностью на M1) вместо входа по level на M5 с внутрибарным lookahead; `review_matrix` принимает `m1_candles` (без них T1M даёт `NO_ENTRY/no_m1`), `_hb_build_matrix` догружает M1-ряд того же окна, старый кеш `hb_reviews` с T1 инвалидируется по составу входов, `/review` по умолчанию `entry=T1M`, real-trade маппинг `confirmation_bars_required` 0 → `T1M` (ближайший по смыслу к req=0)

## v1.2.2 (2026-09-11)

### Added
- **Winrate rows in PnL report** (`web/report.html`): each group subtotal (month/week/day/side) now renders a second row with per-variant winrate `xx.x% (wins/decided)` for all T1/SL1 … T3/SL3 columns plus the "Лучший" column; footer gains a second `WR столбец (winrate)` row with whole-report winrate per entry type (no grouping needed); win = PnL > 0, flat (0 = no entry) excluded from denominator, `—` when no decided trades, green ≥50% / red <50%

### Changed
- **Version sync**: `VERSION` 1.2.1 → 1.2.2, `pyproject.toml` 1.2.1 → 1.2.2

### Fixed
- **Ruff lint cleanup** (`src/level_tester/api/app.py`, `tests/test_hourbounce.py`): moved `VERSION` file reading below imports (E402), removed unused `compute_all_metrics` import (F401), unused `n_variants`/`total_signals`/`side_norm` locals (F841), duplicate `watch_start` dict key (F601), dead `json` import + `try/pass` in `_hb_signal_json`; renamed ambiguous `l` → `lo` in tests (E741). `ruff check src tests` passes, 82 tests green

## v1.2.1 (2026-09-11)

### Added
- **Potential PnL on every HourBounce chart** (`src/level_tester/api/app.py`, `web/hourbounce.js`): `_hb_result_json` returns side-signed `pnl_pct`; single review shows `PnL … · R …` in result line + leading `PnL` metric; matrix 3×3 footers show `in · out · R · PnL +x.xx%` (green/red); export HTML cards include `PnL`; stale cached cells backfilled via `_hb_cell_pnl`
- **Real archive PnL in selection column** (`src/level_tester/api/app.py`, `web/hourbounce.js`): `/api/hourbounce/signals` returns `entry_arch/exit_arch/pnl_arch/pnl_pct_arch/real_trade`; signal list shows `PnL арх. +x.xx%` + `real T·SL` hint; matrix aggregate bar shows `реальная сделка ★ T·SL · арх. PnL`
- **Yellow real-trade frame in matrix** (`web/hourbounce.html`, `web/hourbounce.js`, `src/level_tester/api/app.py`): `_hb_real_trade_info`/`_hb_archive_trade` map archive trade to grid (`confirmation_bars_required` 0/1/2 → T1/T2/T3, `stop_loss_pct` or `|sl-entry|/entry` → nearest SL1/SL2/SL3, only for `closed_*`); matching cell gets `real-trade` outline + `★ real` tag + tooltip; combined best+real double outline; legend entry

### Changed
- **Script cache-buster**: `hourbounce.js?v=5` → `v=6`
- **Version sync**: `VERSION` 1.2.0 → 1.2.1, `pyproject.toml` 1.2.0 → 1.2.1

## v1.2.0 (2026-09-11)

### Added
- **STOP TAKE filter — worked situations** (closed by stop or take, any side): HourBounce outcome dropdown (`web/hourbounce.html`, `web/hourbounce.js`), Report side dropdown (`web/report.html`), Backtest "All Trades" result filter (`web/backtest.html`, `web/backtest.js` — matches `stop_loss`/`take_profit`/`trailing_stop`)
- **TP/SL in percent in HourBounce signal list** (`web/hourbounce.js`): TP/SL shown as price + signed % vs level, side-aware (SHORT mirrored); archive TP JSON-array (`'[0.3683]'`) parsed to first price
- **Matrix 3×3 best-cell highlight** (`web/hourbounce.html`, `web/hourbounce.js`): max PnL among closed (TAKE/STOP) cells — green frame for the most profitable, red frame for the smallest loss when all 9 cells closed by stop
- **HourBounce nav link** on the main page (`web/index.html`)

### Changed
- **Price scale fit** (`web/hourbounce.js`, export template in `src/level_tester/api/app.py`): `calcCenterRange` (level-centered ±15%) replaced with `calcFitRange` — dataset min at the bottom edge, max at the top edge (candles + SL/entry/exit/trail lines); applied to single chart, all 9 matrix cells and exported HTML
- **Chart window +70%** (`web/hourbounce.js`, `src/level_tester/api/app.py`): review/matrix request `pre=51&post=51` (was 30/30); placement fallback (no touch) `sig_idx+96` → `+163`
- **Report footer in cents** (`src/level_tester/api/app.py`, `web/report.html`): footer and group subtotals rounded to 2 decimals to match displayed cells; TOTAL under "Лучший" is now the sum of per-row best PnL (was the sum of all 9 cells); rows carry `best_pnl`
- **Report group subtotal alignment** (`web/report.html`): subtotal rows account for the "Arch факт" column
- **Removed Vol/NATR placeholders** from HourBounce signal cards (`web/hourbounce.js`): neither is stored in `signal_archive` (columns or metadata) nor in `trading.db`
- **Script cache-busters**: `hourbounce.js?v=5`, `backtest.js?v=2`
- **Version sync**: `VERSION` 1.1.0 → 1.2.0, `pyproject.toml` 1.0.2 → 1.2.0

## v1.1.0 (2026-09-11)

### Added
- **HourBounce Review Lite** (`web/hourbounce.html`, `web/hourbounce.js`): Interactive replay review of bounce signals from PGv2 archive with signal table (name, symbol, side, level, date, volume, NATR, archived TP/SL, archived outcome), interactive M5 chart with touch/confirm/entry/exit markers, trail line, stepper (touch → confirm → entry → trail/SL), metrics (R, max+, MAE/MFE), event table, and 3×3 matrix mode (T1/T2/T3 × SL1/SL2/SL3) with sync crosshair and aggregate stats
- **Signal-level ExecParams** (`src/level_tester/backtester/hourbounce.py`): `ExecParams` dataclass with SL, TP, Breakeven (trigger/lock), Trailing (activation/distance/threshold, tp_only), SL source tracking (archive/config); `exec_from_signal()` reconstructs per-signal params from PGv2 archive (handles mutated SL from BE/trailing via config fallback)
- **PGv2 1:1 execution mode** (`entry_code="PG"`): Engine replicates PGv2 `ConfirmationLoop` logic — touch candle counts as first confirmation, `required_bars` consecutive directional closes (close > open for LONG, close < open for SHORT), entry on next open, BE → trail activation (gated by BE) → trail update (threshold) → stop → fixed TP; BE `breakeven_fix_pct` (partial close) not modelled in price replay
- **Signal archive metadata** (`SignalEntry` extended): `confirmation_timeframe`, `trailing_activated`, `sl_moved_to_breakeven`; `exec_from_signal()` infers SL mutation state and falls back to config `stop_loss_pct` (default 1.0%) when SL was moved by BE/trail
- **PnL Report** (`/report`, `web/report.html`, `api/hourbounce/report`): Background job over selected signals × 9 combinations (T1/T2/T3 × SL1/SL2/SL3); per-cell PnL% with side sign (green/red), row best-variant highlight, column footer sums + total; group-by mode (none / side / month / week / day) with per-group subtotals; filter "only traded in DB" (archived `closed_*`); CSV export with `traded/arch_status/arch_pnl/best_variant/best_pnl`; settings persist in `localStorage`
- **HourBounce Review Lite UI** (`web/hourbounce.html`, `web/hourbounce.js`): Matrix/single toggle, execution mode toggle (Grid SL / Signal PGv2 1:1), forced refresh button, BE marker on chart (dashed yellow), execution params panel, date-from/to filters, sort (date/name/symbol), sync crosshair in matrix, CSV export per signal, version badge from `/api/version`
- **Archive trades lookup** (`_hb_archive_trades()`): Batch fetch `closed_*` statuses from `signal_archive.db`; `traded` flag, `arch_status`, `arch_pnl` attached to report rows; UI shows "Arch факт" column with gold dot ● for traded signals
- **Watch start anchor** (`confirmation_waiting_started_at`): Replay scans from `max(dt_place, watch_start)` so PGv2 restarts don't phantom-touch old candles; UI shows "watch_start" alongside `touch_ref`
- **Archive metadata enrichment** (`/api/hourbounce/signals`): `required_bars` from `confirmation_bars_required`, `watch_start` from `confirmation_waiting_started_at`, `pg_available` flag for Signal mode availability
- **HourBounce engine config** (`DEFAULT_CONFIG`): `life_window_t=None` (unbounded — finds nearest actual outcome), `confirm_window_w=20`, PGv2 SL/TP/BE/trail defaults
- **Database schema** (`HbReviewRow`): `hb_reviews` table caches 9-cell payloads per (signal_id, config_fp, lookforward); keyed by `window_last`/`window_count` (not tail), auto-invalidated on config change or gaps inside life window
- **Candle cache** (`load_candles_cached`): M5 candles from DB gaps, Binance fill for holes, end capped at last closed candle (no forming bar)
- **Tests**: 6 new PGv2 scenarios (req=0/1/2, red touch reset, doji, T3 primary), `test_exec_from_signal_mapping` with mutated/pristine SL, 3 new store tests (holes, roundtrip, payload), 82 total tests pass

### Changed
- **HourBounce engine**: Removed 24h life window (`life_window_t=None`), added PGv2 execution mode (`entry_code="PG"` with `pg_required`), BE/trail gating, trail threshold default 0.5%, trail TP only flag
- **SignalReader**: Added `confirmation_timeframe`, `trailing_activated`, `sl_moved_to_breakeven` fields; `_read_archive` populates new fields from metadata
- **ExecParams**: Added `sl_source` tracking ("archive"/"config"), `trail_threshold_pct` (default 0.5% PGv2), `trail_tp_only`
- **API**: `/api/hourbounce/review` accepts `mode=grid|signal`; `/api/hourbounce/matrix` accepts `refresh`; `/api/hourbounce/signals` adds `date_from`/`date_to`, `sort` (date_desc/asc, name_asc/desc, symbol_asc), `pg_available` flag; `/api/hourbounce/export` includes `required_bars` in payload
- **Report API**: `_run_hb_report` attaches `traded`/`arch_status`/`arch_pnl` from `_hb_archive_trades`; rows include `traded`/`arch_status`/`arch_pnl`; CSV adds `traded/arch_status/arch_pnl/best_variant/best_pnl`
- **Report UI** (`web/report.html`): Filter "only traded in DB" (checkbox), group-by select (none/side/month/week/day) with per-group subtotals and traded counts; table shows "Arch факт" column with gold dot ● for traded; "Лучший" column bold for row best; CSV exports `traded/arch_status/arch_pnl/best_variant/best_pnl`; settings (search, side, limit, dates, traded, group) persist in `localStorage`; filter inputs (date from/to, traded checkbox, group select); header adds "→ PnL-отчёт" link
- **HourBounce UI** (`web/hourbounce.html`): Mode toggle (Matrix / Signal), execution mode toggle (Grid / Signal PG 1:1), refresh button, exec params bar (SL/TP/BE/trail), BE dashed yellow line + marker on chart, stepper 4 shows TP/trail/SL, date filters, sort dropdown (date/name/symbol), version badge from `/api/version`, yellow version text in header
- **Database**: `HbReviewRow` table added, `ensure_schema` creates on startup
- **New files**: `src/level_tester/backtester/hourbounce.py`, `hourbounce_store.py`, `db_variant.py`; `web/hourbounce.html`, `hourbounce.js`, `report.html`; `scripts/validate_vs_archive.py`; `tests/test_hourbounce.py`, `test_hb_store.py`; `web/report.html`
- **Documentation**: `tz/` folder with PGv2 spec drafts; `docs/` with analysis notes

### Fixed
- **PGv2 SL fallback**: `exec_from_signal` now uses config `stop_loss_pct=1.0%` when archive SL was moved by BE/trail (detected via `trailing_activated` / `sl_moved_to_breakeven`); `sl_source` tracks origin ("archive"/"config")
- **PGv2 confirmation logic**: Touch candle counts as first confirmation (req=2 → touch + 1 more); red touch resets counter; req=0 market-on-touch with direction; req=1 needs 1 green after touch; doji resets; matches PGv2 `ConfirmationLoop` (touch candle included in consecutive count)
- **Report CSV**: Fixed duplicate `anyTrade` declaration; `bestOf` in CSV uses per-row `bestOf` helper
- **Report JS**: Removed duplicate `anyTrade` declaration; `bestOf` helper for CSV
- **PGv2 entry code "PG"**: T1/T2/T3 entry codes still work; "PG" uses archive params + PGv2 rules
- **SignalReader**: Added `confirmation_timeframe`, `trailing_activated`, `sl_moved_to_breakeven` to `_read_archive` and `_row_to_signal`
- **ExecParams**: `trail_threshold_pct` default 0.5 (PGv2 default); `trail_tp_only` flag
- **SignalReader**: `_read_trading` sets new fields to `None` for trading.db (no mutation state)

### Removed
- None (backwards compatible — existing grid T1/T2/T3 × SL1/SL2/SL3 still works)

### Changed
- Patch version bump (1.0.1 → 1.0.2)
- Synced `pyproject.toml` version with `VERSION` file
- Visual mode refinements in `src/level_tester/api/app.py`, `web/app.js`, `web/backtest.js`, `web/styles.css`

## v1.0.1 (2026-09-04)

### Fixed
- **Breakeven not working**: Fixed critical bug where breakeven stop was being overwritten with original stop price, making breakeven ineffective (`trade_model.py:127-128`)
- **No-entry signals counted as losses**: Signals without entry confirmation are now tracked separately with `pnl=0` instead of being filtered out or counted as losses
- **Winrate calculation**: Now excludes no-entry signals from denominator, showing true winrate of executed trades
- **Version consistency**: Updated `pyproject.toml` version to match `VERSION` file (1.0.0), index.html now reads version dynamically from API

### Added
- **Trade visual mode**: Double-click any trade in backtest "All Trades" table to open replay window with trade parameters pre-filled (symbol, date, entry/SL/TP, trailing, breakeven)
- **Visual trade levels on chart**: Entry (green), Stop Loss (red), Take Profit (blue), Trailing activation (orange dashed), Breakeven (yellow dashed) displayed as price lines
- **Auto-open detail chart**: When replay cursor approaches entry price ±0.5%, detail chart (M1/M5) automatically opens for visual verification
- **No-entry tracking**: Added `no_entry` counter in metrics, trades table, and CLI reports; "No Entry" filter in backtest UI
- **Variant parameters in trade data**: Trailing, breakeven, and partial close parameters now included in backtest results for visual mode

### Changed
- **Loss filter**: Now `pnl < 0` (excludes `pnl=0` no-entry trades)
- **Equity curve**: Skips no-entry trades
- **Side winrates**: Exclude no-entry trades
- **CLI backtest output**: Shows "No entry: N" count in summary
- **HTML report**: Added "No Entry" column, excludes no-entry from side/symbol winrates
- **Dynamic version in index.html**: Page title and header version now fetched from `/api/version`

### Fixed

- **Backtest 422 error on empty inputs**: `lookback` and `lookforward` fields now clamp to valid ranges with fallback defaults instead of sending `null` (parseInt on empty string → NaN → JSON null)
- **Error messages in UI**: Pydantic validation errors now display as human-readable text instead of `[object Object]`

### Added

- **Smart load validation**: backtest validates `signals × methods` API calls against a 2000-call limit (~50 min estimated time); exceeding shows actionable hint (e.g. "reduce signals: 800, methods: 3")
- **Live stats bar**: below signal selection shows loaded/selected signals, variants, methods, API call count, and estimated runtime — updates in real time as parameters change
- **Percentage precision**: winrate, long/short winrate, max drawdown now display with 2 decimal places
- **PnL chart labels**: variant names always visible on the PnL by Variant bar chart (`autoSkip: false`)

### Changed

- Removed hard `max_length=200` limit on `signal_ids`; replaced with load-based validation
- Cache buster updated to `?v=fix-422-error`

## v0.5.1 (2026-09-03)

### Fixed

- **Backtester entry logic**: rewritten to use M5 timeframe with proper touch/confirmation semantics:
  - Type 0 (Touch): LIMIT order at entry price fires on touch (candle wick reaches level, close > entry, green for LONG)
  - Type 1 (1 bar): MARKET on next open after 1 confirming bar (close > entry + green)
  - Type 2 (2 bars): MARKET on next open after 2 consecutive confirming bars within `max_wait` window
  - Invalid touch (wick touches but close doesn't confirm) is skipped, search continues
- **20% distance exit**: trades automatically close with reason `level_invalidated` when price moves 20% away from entry without hitting SL/TP
- **Candle loading**: switched to M5 timeframe, removed 50-bar lookback (signal already validated), increased lookforward to 1000 bars with automatic reload when exhausted
- **Symbol grouping footer**: now shows best variant+method per symbol instead of aggregating all trades
- **Table sorting**: headers now cycle through Descending → Ascending → Disabled (original order) → Descending

### Changed

- Removed `confirmation_max_wait` parameter from API (was `confirmation_max_wait_bars`); max wait only applies to confirmation phase, not trade management
- Updated default backtest parameters: `lookback=0`, `lookforward=1000`, `confirmation_bars` range 0-20
- Frontend: removed "Max wait bars" field; added verbose console logging (`[Backtest]` prefix) for grouping, symbol best-variant selection, and footer rendering
- Cache-buster added to backtest.js (`?v=grouping-debug-2`)

### Added

- Try/catch wrapper around grouping switch with error display in UI
- Debug table logging in browser console for per-symbol candidate evaluation

### Tests

- All 44 tests pass

## v0.5.0 (2026-09-02)

### Added

- **Backtester module** (`src/level_tester/backtester/`): full backtesting engine with signal reader, candle loader, trade model, entry types, variants, and metrics computation
- **Backtester web UI** (`web/backtest.html`, `web/backtest.js`): signal selection, variant editing table, equity curve chart, PnL bar chart, trade log with filters
- **Confirmation methods**: 3 entry confirmation methods tested simultaneously — Touch (0), 1 bar (1), 2 bars (2)
- **PnL in %**: all metrics, equity curve, and trade table display profit/loss as percentage
- **Save/Load settings**: export/import all backtest configuration to/from JSON file with native file picker
- **Summary table sorting**: click column headers to sort results ascending/descending
- **Grouping option**: toggle between per-variant aggregation and per-symbol best-variant view
- **Signal source filters**: separate checkboxes for Archive and Trading signal databases
- **Symbol checklist**: multi-select symbol filter populated from loaded signals with search
- **Version display**: version number shown in terminal on startup and on backtester page header
- **API endpoint**: `GET /api/version` returns current version; `GET /api/backtest/signals` supports `include_trading`/`include_archive` params

### Changed

- `VariantMetrics` extended with `total_pnl_pct`, `avg_pnl_pct`, `avg_win_pct`, `avg_loss_pct`, `expectancy_pct`, `equity_curve_pct`
- `TradeResult` dataclass gained `method` and `method_label` fields for confirmation method tracking
- `SignalReader.read()` accepts `include_trading` parameter (default `False`)
- Entry confirmation logic handles `required_bars=0` for immediate touch entry

### Fixed

- Test assertions updated to match current config values (`wing=8`, `default_speed=1.0`)

## v0.4.6 (2026-08-26)

### Added

- **Per-instrument price standard.** Levels now use each instrument's exchange `tick_size` (from Binance `PRICE_FILTER.tickSize`) instead of a fixed `0.01`, and the display precision (`pricePrecision`) is carried through to the UI. This fixes inaccurate level detection on low-priced coins (e.g. XRP): prices quantize to the real tick (0.0001 → exact 0.5234) and the zone is the intended ~0.8% instead of a coarse ~1.9%
- `InstrumentRow` gained `tick_size` and `price_precision` columns; `ensure_schema` back-fills them on existing databases via `ALTER TABLE`
- `price_precision_from_tick` helper derives display precision from a tick size when the exchange does not supply it

### Changed

- Engine uses the instrument `tick_size` in `LevelConfig` (`api/app.py`); `tick_size`/`price_precision` are exposed on every replay snapshot
- Web UI applies the instrument `priceFormat` (precision + minMove) to both H1 and detail charts and formats level prices/zones with `fmtPrice`, dropping redundant decimal places

### Tests

- Added coverage for exchange filter parsing (`_extract_price_spec`) and instrument sync persistence of `tick_size`/`price_precision`

## v0.4.5 (2026-08-26)

### Changed

- **Level touch now means reaching the level line, not the zone band**. In `LevelBook.evaluate` (`src/level_tester/domain/search/levels.py`) a `level.touched` event (and the resulting pause) fires only when the candle wick reaches `level.price`; the surrounding zone is no longer treated as a touch. The `level.approaching` hint still uses the zone band
- Aligned touch-time refinement in `EntryConfirmation._refine_touch` (`src/level_tester/domain/confirmation/service.py`) to the same level-line definition

### Fixed

- **Web replay pause/detail start**: removed the `priceRemainsInZone` guard in `autoPlayStep` (`web/app.js`) so a pause and detail view trigger on any `level.touched`, including H1 candles whose close later leaves the zone. The detail window now starts from the touching candle's `open_time` (`state.detailPrevMs`), so the M1/M5 replay begins exactly at the touch

## v0.4.4 (2026-08-26)

### Fixed

- **Detail chart on repeated runs**: added `destroyDetailChart()` that fully removes the chart instance (`chart.remove()`) and resets `state.detailChart` / `state.detailSeries`; called from `resetDetailChart()` and `clearDetailViewData()` instead of only clearing the series. This fixes the detail chart rendering without time/price scales and the TV marker after a second "New replay" (H1 kept working because it re-`setData`s every step)
- **Intermittent detail rendering**: `enablePriceAutoScale()` and `scrollChartToRight()` moved out of the per-candle loop in `animateDetailCandles` and called once after the whole batch, so they no longer race with the chart draw cycle
- **Viewport fallback**: `scrollChartToRight()` now falls back to `timeScale.fitContent()` when `scrollToPosition` is absent from the published 5.2.1 build

## v0.4.3 (2026-08-26)

### Changed

- **Lightweight Charts v5**: CDN pinned to `lightweight-charts@5` (5.2.1); series created via `chart.addSeries(CandlestickSeries, ...)`, pivot markers moved to `createSeriesMarkers()` primitive, `ResizeObserver` replaced by `autoSize: true`
- **Right-edge anchoring**: H1 and detail charts use native `timeScale.scrollToPosition(5, false)` — zoom is preserved, the newest candle appears at the right edge while older bars shift left (no manual `setVisibleLogicalRange`, no compression)
- **Detail animation**: each new M1/M5 candle is drawn individually per Step via accumulated `setData()` (`state.detailBars`); animation state resets cleanly on Reset and Play
- **Step flow**: detail sync runs on every Step including the warm-up phase; the obsolete `beforeDisplay` gate was removed from the step handler

### Fixed

- **Price scale after symbol switch**: price autoscale is re-enabled per series via `priceScale().setAutoScale(true)` (correct v5 API), so switching BTC → ZEC rescales instead of keeping BTC-level prices off-screen
- **Single-candle rendering**: removed calls to APIs absent from the published 5.2.1 build (`scrollToRealtime`, `priceScale().fit()`) and the invalid `autoScroll` chart option that aborted drawing after the first candle
- **H1 initial view**: first load paints all candles visible at the current replay cursor and anchors them to the right edge

## v0.4.2 (2026-08-26)

### Added

- **Config endpoint**: `GET /api/config` exposes replay defaults (`default_speed`, `detail_timeframes`, `default_detail_timeframe`) to the frontend
- **Config-driven page defaults**: Speed field and detail TF options are populated from `config/default.yaml` on page open; saved TF choice keeps priority over the default
- **Market configuration**: `market.*` from YAML is now applied — `market_type` selects the Binance endpoint family (USDT-M `fapi` / COIN-M `dapi`, env override still wins), `quote_asset` drives instrument filtering and seeded instrument rows

### Changed

- **Step order**: detail M1/M5 candles animate first, the H1 candle is drawn after the animation completes
- **Continuous detail chart**: per-tick `fitContent()` removed; candles append via `update()`, previous segments continue across Steps, viewport re-anchored once per hour via `resetTimeScale()` with `autoScroll`
- **Pause condition**: auto-pause on touch now triggers only when the H1 close remains inside the level zone; a touch followed by an exit no longer stops playback
- **Detail data window**: snapshot filter uses `calculation_from`, so M1/M5 data is available during the warm-up phase before `display_from`
- **Run speed**: new runs start with `replay.default_speed` instead of hardcoded 1.0

### Fixed

- **Animation aborts**: replaced unavailable `timeScale.scrollToRealtime()` (LightweightCharts 4.2.3) that crashed the loop after the first candle; animation generations cancel cleanly, draw errors surface in the detail status instead of being swallowed

### Configuration

- `replay.default_speed` raised to 10.0

## v0.4.1 (2026-08-26)

### Added

- **Detail timeframe selector**: M1/M5 picker for the detail window; choice persisted in `localStorage`, applied to the next created replay, panel title follows the selection
- **Levels on the detail chart**: visible active levels drawn as price lines (center + zone edges) on the M1/M5 chart, synced with the H1 view
- **Play/Step detail behavior**: Play clears the M1/M5 chart and runs H1 until the next pause; Step re-animates minute candles after pause; detail activation persists for the whole run
- **Speed stepper**: speed input now increments by 1 (was 0.1)

### Fixed

- **Detail chart zoom**: animation no longer calls `fitContent()` on a single candle (which stretched 1–2 candles across the whole panel); the visible range is preset to the full hour and `scrollToRealtime()` keeps the current price in view
- **Animation cancellation**: pressing Play during M1/M5 animation no longer leaves a background loop drawing candles; animation generations are cancelled via a sequence counter

## v0.4.0 (2026-08-26)

### Added

- **Split chart view**: screen divided into 3/4 H1 chart and 1/4 detail M1/M5 chart, synchronized in parallel
- **Detail chart activation**: M1/M5 panel opens on level touch and replays minute candles one-by-one at the configured speed
- **Replay progress indicator**: progress bar with `sequence / total (%)` readout in the toolbar; snapshot now exposes `total_candles` (full replay size including warm-up window)
- **Situative status hints**: toolbar message on pause ("press Play/Step to continue") and finish; appears at the event moment and clears on next command
- **Warm-up fast-forward**: replay steps before `display_from` run without delay so playback starts at the first visible candle

### Changed

- **Layout**: full-screen width layout; control fields (symbol, display from, speed, search, min volume, refresh) merged into a single row
- **Removed panels**: Event journal and Detail timeframe cards removed from the UI; detail timeframe fixed to `1m`
- **Cursor timezone**: cursor readout now displays UTC matching the chart time scale

### Fixed

- **Null reference errors**: removed JS references to deleted UI elements (`detail-timeframe`, `detail-count`, `events`, `detail`) that crashed "New replay"

## v0.3.1 (2026-08-26)

### Added

- **Modular domain packages**: configuration, search, confirmation, execution and evaluation boundaries
- **Entry confirmation pipeline**: H1 touches are refined against M1/M5 candles and confirmed setups are created after the configured candle pattern
- **Virtual execution**: confirmed setups now produce trades with entry, stop, take, fees, slippage and PnL
- **Trade events**: replay snapshots and event history include `entry.confirmed`, `trade.opened` and `trade.closed`
- **Module documentation**: added `docs/MODULES.md` describing responsibilities and dependency rules
- **Trade pipeline tests**: added coverage for detail-touch refinement and take-profit execution

### Changed

- **Replay orchestration**: `ReplayEngine` coordinates search, confirmation, execution and evaluation modules
- **Detail data handling**: lazily loaded M1/M5 candles are merged and can be processed after the H1 touch event
- **Configuration**: added confirmation and execution settings to `config/default.yaml`; selected detail timeframe is applied to confirmation
- **Domain layout**: pivot/level logic moved under `domain/search`, and outcome logic under `domain/evaluation`

### Refactored

- **Code quality**: applied the project Ruff rules across source and test code

### Fixed

- **Next-bar entry boundary**: a candle opening exactly at confirmation close is now recognized as the next entry candle
- **Late touch refinement**: H1 touch events now retain their source candle window for exact M1/M5 refinement

## v0.3.2 (2026-08-26)

### Added
- **Symmetric level zones**: zone boundaries now quantized to tick size and centered on median pivot price, ensuring zone_low/zone_high are price-symmetrical
- **Level state visualization**: central price line reflects level state (confirmed: bold, CREATED: dashed); zone edges use muted edge colors
- **Level line cleanup on reset**: all price lines and markers cleared on reset, preventing ghost lines
- **Resize-aware chart**: chart resizes on window dimension changes
- **Regression tests**: added `tests/test_search_levels.py` covering quantized median clustering and CREATED→CONFIRMED lifecycle
- **Level confirmation event**: `level.confirmed` emitted when level transitions from CREATED to CONFIRMED

### Changed
- **Zone recalculation**: `LevelBook.add_pivot` now uses `quantize_tick` for price and computes symmetric zone width from quantized price
- **Level event semantics**: second pivot on same level returns `created=False`; `level.confirmed` event replaces erroneous duplicate `level.created`
- **Frontend level rendering**: `renderLevelLines` creates zone edges + central price line per level; `clearLevelLines` removes all before re-render

### Fixed
- **Level zone symmetry**: zone boundaries now consistently centered on quantized median price after tick rounding
- **Level state events**: `level.confirmed` emitted only on CREATED→CONFIRMED transition; no duplicate emission on subsequent pivots
- **Ruff style fixes**: resolved 3 FURB157 warnings in test fixtures

## v0.3.0 (2026-08-26)

### Added

- **Approach detection**: `level.approaching` event emitted when price enters the approach zone before a touch
- **Auto-pause on touch**: replay stops automatically when an active level is touched
- **Version display**: version number shown in page title and topbar header

### Changed

- **Level algorithm aligned with MVP_1H scanner**: `wing=6`, `zone_percent=0.008`, `min_bounce_percent=0.045`, `min_touches=2`, `breakout=wick`
- **Pivot volume filter**: global average volume ratio `min_volume_ratio=0.5` replaces local neighbour average
- **Pivot time**: `pivot_time` uses `open_time` instead of `close_time` to match chart candle position
- **Level price**: recalculated as median of cluster pivot prices instead of expanding zone
- **Level touch detection**: resistance checks `high`, support checks `low` (not broad range overlap)
- **Level states**: levels transition `CREATED → CONFIRMED → WAITING_TOUCH → TOUCHED → BROKEN`
- **Chart rendering**: incremental `update()` with `rightOffset`/`barSpacing` instead of `fitContent()`; proper scrolling
- **Frontend level display**: only confirmed levels shown on chart and in panel; broken/expired removed on reset

### Fixed

- **Pivot marker offset**: high/low markers no longer shifted one candle to the right
- **Broken levels not removed**: broken levels now cleared from chart and level panel
- **Reset cleanup**: price lines and markers cleared on reset

### Configuration

- Algorithm defaults in `config/default.yaml` now match scanner production values

## v0.2.0 (2026-08-25)

### Added

- **Domain layer**: `Candle`, `Pivot`, `Level`, `LevelEvent` models; `CausalPivotDetector`, `LevelBook`, `OutcomeEvaluator`, `ReplayEngine`
- **Application layer**: `InstrumentService` (Binance catalog sync), `DataIngestionService` (candle ingestion with coverage tracking), `RunService` (replay lifecycle management)
- **Infrastructure layer**: `BinanceFuturesClient` (HTTP client with pagination/dedup), `InstrumentRepository`, `CandleRepository`, `RunRepository`, `DataQualityReport`
- **FastAPI API**: `/api/status`, `/api/instruments`, `/api/instruments/sync`, `/api/instruments/{symbol}/coverage`, `/api/runs` (CRUD + commands), WebSocket events
- **Frontend**: candlestick chart (LightweightCharts), instrument search/filter, replay controls, level display, event journal
- **Progress bar with loading stages**: `searching instrument` → `loading candles` → `validating data` → `initializing engine`
- **SQLAlchemy persistence**: MariaDB/SQLite support, auto-schema creation, UTC timezone enforcement
- **MCP Context7 integration**: documentation lookup for library APIs
- **Test suite**: unit tests for persistence, instruments, data quality, API, causal engine
- **MariaDB initialization script**: `scripts/init_mariadb.ps1`
- **SQL migrations**: `migrations/001_initial.sql`

### Changed

- Date input simplified to date-only picker (no hours/minutes)
- Volume display in abbreviated format ($1.2B, $450M, $12K)

### Configuration

- Added `config/default.yaml` with replay algorithm parameters
- Added `.env.example` with `DATABASE_URL`, `BINANCE_FUTURES_BASE_URL`
- Added `opencode.json` with Context7 MCP server
