# Changelog

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
