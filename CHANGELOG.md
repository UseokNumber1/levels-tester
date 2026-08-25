# Changelog

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
