# Changelog

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
