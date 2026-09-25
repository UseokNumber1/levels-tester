# AGENTS.md

## Setup

From the project root (Windows PowerShell unless noted):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

The one-shot runner (`.\run.ps1` / double-click `run.bat`) auto-creates the
venv and installs dependencies on first run by importing `level_tester.api.app`;
skip the manual install if you use it.

Copy secrets before first run:

```powershell
copy .env.example .env   # edit .env — it is git-ignored
```

- `DATABASE_URL` — MariaDB (`mysql+pymysql://...`) or SQLite fallback
  (`sqlite:///./levels_tester.db`). Without a DB the app still boots.
- `BINANCE_FUTURES_BASE_URL` — public klines endpoint, no API key required.
- `APP_HOST`/`APP_PORT`/`APP_ENV` — defaults `127.0.0.1` / `8080` / `development`.

## Database init (MariaDB)

Run once per machine:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\init_mariadb.ps1
```

- Reads `DATABASE_URL` from `.env`, asks for the MariaDB root password once
  (never stored), creates the DB/user, and applies `migrations/001_initial.sql`.
- The app user is forced to `mysql_native_password`; pymysql cannot use the
  MariaDB 11.8 GSSAPI plugin, so a normal native-password user is required
  (hard failure otherwise).
- `DATABASE_URL` password with `@:/#%?` must be URL-encoded (see `.env.example`).
- SQLite fallback needs no init; the file is created automatically.

The app auto-creates tables if missing and checks `GET /api/status`
(`database`, `database_schema`, `binance`) for live state.

## Run / serve

```powershell
.venv\Scripts\python -m uvicorn level_tester.api.app:app --reload
# or
.\run.ps1
```

- Health: `http://127.0.0.1:8080/health`; UI: `http://127.0.0.1:8080/`.
- Algorithm parameters live in `config/default.yaml` and are loaded by
  `level_tester/settings.py:load_replay_config`. Secrets never come from YAML.

## Verify

```powershell
pytest -q
ruff check src tests
```

- Tests are isolated from the real `.env` by `tests/conftest.py`, which sets
  `DATABASE_URL` to a temp SQLite path before collection.
- No `mypy`/typecheck step is configured; only ruff (line-length 100, py311,
  ignore `B008`).
- `scripts/validate_vs_archive.py` compares engine output against PGv2 closed
  trades; it has hardcoded DB paths under `E:\Pyton_project\precision_grid_v2`
  and requires that sibling project to exist — skip in CI. Run with
  `.venv\Scripts\python scripts\validate_vs_archive.py --limit 15`.

## Layout

```text
src/level_tester/
  api/app.py            FastAPI entry, serves UI + /api/* REST + WS events
  application/          run_service, candle_service, ingestion, instruments
  domain/               configuration, search, confirmation, execution, evaluation, replay
  infrastructure/         binance, database, repositories, instruments, quality
  backtester/           hourbounce replay engine + metrics (legacy, used by validate_vs_archive)
tests/                  pytest tests + fixtures/golden_replay.json
migrations/001_initial.sql  MariaDB schema (instruments, candles, runs, levels, events, outcomes)
config/default.yaml         algorithm/replay/market settings
docs/SPECIFICATION.md       pivot/level/touch/breakout/outcome rules
docs/MODULES.md             module boundaries + dependency rules
docs/IMPLEMENTATION_PLAN.md phased plan, checkpoints, acceptance criteria
```

## Architecture constraints (do not violate)

- `search` must not import `confirmation`/`execution`/`api`/`web`.
- `confirmation` does not open trades; `execution` does not fetch Binance data;
  `evaluation` does not mutate level/trade state.
- `replay` is the single owner of historical time; modules run in order
  `search -> confirmation -> execution -> evaluation` per H1 bar. No reads of
  candles to the right of the cursor; never use `datetime.now()` as event
  time. See `docs/IMPLEMENTATION_PLAN.md` §17 §23.
- Replay is deterministic on closed candles; speed only affects event
  delivery rate, not computation.

## Operational gotchas

- The legacy `run_server.py` entrypoint imports `backend.app` and runs on
  port `8010` — it is NOT the current app. The real API is
  `level_tester.api.app:app`.
- `package.json` only declares frontend JS deps; there is no build script or
  separate dev server (UI is served by FastAPI).
- There is no CI, no pre-commit hooks, and no `.github` directory — lint/tests
  are run manually.
