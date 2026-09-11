from __future__ import annotations

import logging
import os
import re
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Thread

_APP_DIR = Path(__file__).resolve().parents[3]
_VERSION_FILE = _APP_DIR / "VERSION"
APP_VERSION = _VERSION_FILE.read_text().strip() if _VERSION_FILE.exists() else "0.0.0"
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from level_tester.application.ingestion import DataIngestionService
from level_tester.application.instruments import InstrumentService
from level_tester.application.run_service import RunService
from level_tester.backtester.backtest_engine import BacktestEngine
from level_tester.backtester.entry_types import EntryType
from level_tester.backtester.metrics import compute_all_metrics, compute_all_metrics_by_key
from level_tester.backtester.signal_reader import SignalReader
from level_tester.backtester.variants import BUILTIN_VARIANTS, get_builtin
from level_tester.domain.confirmation import SUPPORTED_CONFIRMATION_METHODS
from level_tester.domain.models import Candle
from level_tester.domain.search.levels import price_precision_from_tick
from level_tester.infrastructure.binance import BinanceFuturesClient
from level_tester.infrastructure.database import (
    InstrumentRow,
    check_database,
    create_session_factory,
    ensure_schema,
)
from level_tester.infrastructure.instruments import InstrumentRepository
from level_tester.infrastructure.repositories import CandleRepository, RunRepository
from level_tester.settings import get_settings, load_replay_config

logger = logging.getLogger(__name__)


class CandleInput(BaseModel):
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    timeframe: str = "1h"
    is_closed: bool = True

    def to_domain(self) -> Candle:
        return Candle(**self.model_dump())


class RunCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9]+$")
    display_from: datetime
    calculation_from: datetime | None = None
    effective_to: datetime | None = None
    detail_timeframe: str = Field(default="1m", pattern=r"^(1m|5m)$")
    confirmation_methods: list[str] = Field(default_factory=lambda: ["bounce"])
    confirmation_required_bars: int = Field(default=2, ge=1, le=100)
    confirmation_max_wait_bars: int = Field(default=15, ge=1, le=500)
    # Optional closed candles are useful for local imports and deterministic tests.
    seed_candles: list[CandleInput] = Field(default_factory=list)

    @field_validator("display_from", "calculation_from", "effective_to")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must include a timezone")
        return value

    @field_validator("confirmation_methods")
    @classmethod
    def validate_confirmation_methods(cls, value: list[str]) -> list[str]:
        methods = [method.lower() for method in value]
        if (
            not methods
            or len(set(methods)) != len(methods)
            or not set(methods) <= set(SUPPORTED_CONFIRMATION_METHODS)
        ):
            raise ValueError(
                "confirmation_methods must contain unique methods from: "
                + ", ".join(SUPPORTED_CONFIRMATION_METHODS)
            )
        return methods

    @model_validator(mode="after")
    def validate_confirmation_limits(self) -> "RunCreate":
        if (
            self.confirmation_max_wait_bars < self.confirmation_required_bars
        ):
            raise ValueError("confirmation_max_wait_bars must be >= confirmation_required_bars")
        return self


class SpeedUpdate(BaseModel):
    speed: float = Field(gt=0, le=100)


settings = get_settings()
config_path = Path(__file__).resolve().parents[3] / "config" / "default.yaml"
default_config = load_replay_config(config_path)

# The market section of default.yaml selects the endpoint family; an explicit
# BINANCE_FUTURES_BASE_URL environment override always wins.
MARKET_BASE_URLS = {
    "usdt_m_futures": "https://fapi.binance.com",
    "coin_m_futures": "https://dapi.binance.com",
}
binance_base_url = os.environ.get(
    "BINANCE_FUTURES_BASE_URL",
    MARKET_BASE_URLS.get(default_config.market.market_type, settings.binance_futures_base_url),
)
service = RunService(default_config)
session_factory = create_session_factory(settings.database_url)
binance_client = BinanceFuturesClient(binance_base_url)
instrument_repository = InstrumentRepository()
instrument_service = InstrumentService(binance_client, instrument_repository)
ingestion_service = DataIngestionService(binance_client)
candle_repository = CandleRepository()
run_repository = RunRepository()


@asynccontextmanager
async def lifespan(application: FastAPI):
    print(f"Levels Tester v{APP_VERSION} starting...")
    # An unreachable SQL server (or missing database) must not prevent startup;
    # check_database() reports the exact state to /api/status and the UI.
    try:
        ensure_schema(session_factory)
    except Exception:
        logger.debug("database schema initialization failed", exc_info=True)
    database_check = check_database(session_factory)
    application.state.database_check = database_check
    application.state.catalog_sync = "not_started"
    if database_check.connected and database_check.schema_valid:
        # The catalog sync must never block server startup; its result is visible
        # through /api/status and the instruments list refreshes on demand.
        def background_sync() -> None:
            try:
                _sync_catalog()
                application.state.catalog_sync = "completed"
            except Exception as exc:  # noqa: BLE001 - background boundary reports failure in status
                application.state.catalog_sync = f"failed: {exc}"

        Thread(target=background_sync, daemon=True).start()
    else:
        application.state.catalog_sync = "skipped"
    yield


app = FastAPI(title="Levels Tester", version=APP_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8080", "http://localhost:8080"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type"],
)

STATIC_DIR = Path(__file__).resolve().parents[3] / "web"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.app_env}


@app.get("/api/status")
async def server_status() -> dict[str, str]:
    database_check = check_database(session_factory)
    return {
        "backend": "connected",
        "database": "connected" if database_check.connected else "disconnected",
        "database_schema": "valid" if database_check.schema_valid else "invalid",
        "database_timezone": database_check.timezone,
        "binance": "connected" if binance_client.ping() else "disconnected",
    }


@app.get("/api/config")
async def replay_config() -> dict[str, Any]:
    return {
        "replay": {
            "default_speed": default_config.default_speed,
            "detail_timeframes": list(default_config.detail_timeframes),
            "default_detail_timeframe": default_config.detail_timeframe,
            "confirmation_methods": [
                {"id": "touch", "number": 1, "label": "Touch"},
                {"id": "consecutive", "number": 3, "label": "N consecutive closes"},
                {"id": "bounce", "number": 5, "label": "Bounce"},
            ],
        }
    }


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/backtest", include_in_schema=False)
async def backtest_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "backtest.html")


@app.get("/visual", include_in_schema=False)
async def visual_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "visual.html")


@app.get("/hourbounce", include_in_schema=False)
async def hourbounce_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "hourbounce.html")


@app.get("/report", include_in_schema=False)
async def report_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "report.html")


@app.post("/api/instruments/sync")
async def sync_instruments() -> dict[str, Any]:
    try:
        with session_factory() as session:
            count = instrument_service.sync(session)
        return {"status": "ok", "updated": count}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"instrument sync failed: {exc}") from exc


@app.get("/api/instruments")
async def list_instruments(
    search: str = "",
    min_volume: Decimal | None = Query(default=None, ge=0),
    max_volume: Decimal | None = Query(default=None, ge=0),
    quote_asset: str = Query(
        default=default_config.market.quote_asset, min_length=2, max_length=12
    ),
    status: str = Query(default="TRADING", min_length=1, max_length=20),
    limit: int = Query(default=100, ge=1, le=500),
    refresh: bool = False,
) -> dict[str, Any]:
    try:
        with session_factory() as session:
            if refresh or session.query(InstrumentRow).count() == 0:
                instrument_service.sync(session)
            rows = instrument_repository.search(
                session, search, quote_asset, status, min_volume, max_volume, limit
            )
            return {"items": [_instrument_json(row) for row in rows], "count": len(rows)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"instrument list unavailable: {exc}") from exc


@app.get("/api/instruments/{symbol}/coverage")
async def instrument_coverage(
    symbol: str,
    timeframe: str = Query(default="1h", pattern=r"^(1m|5m|1h)$"),
    start: datetime = Query(...),
    end: datetime = Query(...),
) -> dict[str, Any]:
    _validate_range(start, end)
    with session_factory() as session:
        instrument = instrument_repository.find(session, symbol)
        if instrument is None:
            raise HTTPException(status_code=404, detail="instrument is not synchronized")
        coverage = ingestion_service.coverage(session, instrument.id, timeframe, start, end)
        return _coverage_json(coverage)


@app.post("/api/runs", status_code=202)
async def create_run(request: RunCreate) -> dict[str, Any]:
    display_from = _utc(request.display_from)
    effective_to = _utc(request.effective_to) if request.effective_to else _last_closed_boundary()
    calculation_from = (
        _utc(request.calculation_from)
        if request.calculation_from
        else display_from - timedelta(hours=default_config.pivot.wing * 3 + 6)
    )
    try:
        run = service.create_loading(
            request.symbol,
            display_from,
            calculation_from,
            effective_to,
            request.detail_timeframe,
            tuple(request.confirmation_methods),
            request.confirmation_required_bars,
            request.confirmation_max_wait_bars,
        )
        _persist_run(run)
        seed = [item.to_domain() for item in request.seed_candles] or None
        Thread(target=_load_run, args=(run.id, seed), daemon=True).start()
        return service.snapshot(run.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"cannot create run: {exc}") from exc


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    return _snapshot_or_404(run_id)


@app.get("/api/runs/{run_id}/snapshot")
async def get_snapshot(run_id: str) -> dict[str, Any]:
    return _snapshot_or_404(run_id)


@app.get("/api/runs/{run_id}/detail")
async def get_detail(
    run_id: str,
    start: datetime = Query(...),
    end: datetime = Query(...),
) -> dict[str, Any]:
    run = _run_or_404(run_id)
    if run.instrument_id is None:
        raise HTTPException(status_code=409, detail="run has no database instrument")
    _validate_range(start, end)
    if run.engine is None or run.engine.cursor is None:
        raise HTTPException(
            status_code=409, detail="replay cursor has not reached a detail timeframe"
        )
    start = _utc(start)
    end = min(_utc(end), run.engine.cursor.bar_time)
    if start >= end:
        raise HTTPException(
            status_code=422, detail="detail range must be before the current cursor"
        )
    with session_factory() as session:
        coverage = ingestion_service.ensure_range(
            session, run.instrument_id, run.symbol, run.detail_timeframe, start, end
        )
        candles = candle_repository.list_range(
            session, run.instrument_id, run.detail_timeframe, start, end
        )
    if coverage.missing_count:
        raise HTTPException(status_code=503, detail="detail timeframe still has missing candles")
    result = service.attach_detail(run_id, candles)
    _persist_run(service.get(run_id))
    return result


@app.post("/api/runs/{run_id}/{command}")
async def command(run_id: str, command: str) -> dict[str, Any]:
    if command not in {"play", "resume", "pause", "step", "reset", "cancel"}:
        raise HTTPException(status_code=404, detail="unknown replay command")
    try:
        result = service.command(run_id, command)
        _persist_run(service.get(run_id))
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.patch("/api/runs/{run_id}/speed")
async def update_speed(run_id: str, request: SpeedUpdate) -> dict[str, Any]:
    try:
        return service.set_speed(run_id, request.speed)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.websocket("/api/runs/{run_id}/events")
async def events(websocket: WebSocket, run_id: str, after_sequence: int = -1) -> None:
    origin = websocket.headers.get("origin")
    if origin is not None and origin not in {"http://127.0.0.1:8080", "http://localhost:8080"}:
        await websocket.close(code=4403)
        return
    try:
        snapshot = service.snapshot(run_id)
    except KeyError:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    snapshot["events"] = [
        event for event in snapshot["events"] if event["sequence"] > after_sequence
    ]
    await websocket.send_json(snapshot)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        return


# ---------------------------------------------------------------------------
# Backtester API
# ---------------------------------------------------------------------------
PGV2_TRADING_DB = os.environ.get(
    "PGV2_TRADING_DB",
    r"E:\Pyton_project\precision_grid_v2\data\db\trading.db",
)
PGV2_ARCHIVE_DB = os.environ.get(
    "PGV2_ARCHIVE_DB",
    r"E:\Pyton_project\precision_grid_v2\data\db\signal_archive.db",
)

_backtest_jobs: dict[str, dict[str, Any]] = {}
_backtest_counter = 0


class VariantPayload(BaseModel):
    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=80)
    sl_pct: Decimal = Field(gt=0, le=20)
    tp_rr: Decimal | None = Field(default=None, gt=0, le=999)
    tp_pct: Decimal | None = Field(default=None, gt=0, le=100)
    trailing_activation_pct: Decimal | None = Field(default=None, ge=0, le=100)
    trailing_stop_pct: Decimal | None = Field(default=None, ge=0, le=100)
    trailing_update_threshold_pct: Decimal | None = Field(default=None, ge=0, le=100)
    trailing_tp_only: bool = False
    breakeven_trigger_pct: Decimal | None = Field(default=None, ge=0, le=100)
    breakeven_lock_pct: Decimal | None = Field(default=None, ge=0, le=100)
    partial_close_pct: Decimal | None = Field(default=None, ge=0, le=100)
    partial_close_rr: Decimal | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def validate_tp(self) -> "VariantPayload":
        if self.tp_rr is not None and self.tp_pct is not None:
            raise ValueError("tp_rr and tp_pct are mutually exclusive (выберите RR либо %)")
        return self


class BacktestRunRequest(BaseModel):
    signal_ids: list[str] = Field(min_length=1)
    variants: list[str] = Field(default_factory=lambda: [v.id for v in BUILTIN_VARIANTS[:3]])
    custom_variants: list[VariantPayload] = Field(default_factory=list)
    entry_type: str = Field(default="confirmation", pattern=r"^confirmation$")
    lookback: int = Field(default=0, ge=0, le=500)
    lookforward: int = Field(default=1000, ge=20, le=2000)
    limit_offset: float = Field(default=0.2, ge=0, le=5)
    confirmation_bars: int = Field(default=2, ge=0, le=20)
    confirmation_methods: list[int] = Field(default=[0, 1, 2], description="0=touch, 1=1bar, 2=2bars")

    @model_validator(mode="after")
    def validate_variants(self) -> "BacktestRunRequest":
        if self.custom_variants:
            ids = [v.id for v in self.custom_variants]
            if len(ids) != len(set(ids)):
                raise ValueError("custom_variants: duplicate id")
            if "__DB__" in ids:
                raise ValueError("custom_variants: id '__DB__' is reserved for the DB baseline")

        n_signals = len(self.signal_ids)
        n_variants = len(self.variants)
        n_methods = len(self.confirmation_methods) or 1
        n_api_calls = n_signals * n_methods  # свечи грузятся 1 раз на сигнал×метод
        est_seconds = n_api_calls * 1.5  # ~1.5 сек на API-запрос

        MAX_API_CALLS = 2000
        if n_api_calls > MAX_API_CALLS:
            est_min = est_seconds / 60
            parts = []
            if n_signals > 800:
                parts.append(f"сигналов: {n_signals} (рекомендация ≤ 800)")
            if n_methods > 3:
                parts.append(f"методов: {n_methods} (рекомендация ≤ 3)")
            if not parts:
                parts.append(f"сигналов: {n_signals}, методов: {n_methods}")
            hint = "; ".join(parts)
            raise ValueError(
                f"Слишком долго: {n_api_calls} запросов к бирже "
                f"({n_signals} сигналов × {n_methods} методов). "
                f"≈{est_min:.0f} мин. Максимум {MAX_API_CALLS} запросов. "
                f"Уменьшите: {hint}"
            )
        return self


@app.get("/api/version")
async def version() -> dict[str, str]:
    return {"version": APP_VERSION}


@app.get("/api/backtest/signals")
async def backtest_signals(
    symbol: str = "",
    side: str = "",
    period: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    only_working_level: bool = Query(default=True, description="Only signals with a validated working level"),
    include_trading: bool = Query(default=False, description="Include signals from trading.db"),
    include_archive: bool = Query(default=True, description="Include signals from signal_archive.db"),
) -> dict[str, Any]:
    reader = SignalReader(PGV2_TRADING_DB, PGV2_ARCHIVE_DB)
    period_start, period_end = (period.split(":") if ":" in period else (None, None))
    signals = reader.read(
        symbol=symbol or None,
        side=side or None,
        period_start=period_start,
        period_end=period_end,
        limit=limit,
        only_working_level=only_working_level,
        include_trading=include_trading,
        include_archive=include_archive,
    )
    return {
        "items": [
            {
                "signal_id": s.signal_id,
                "symbol": s.symbol,
                "side": s.side,
                "entry_price": s.entry_price,
                "stop_loss": s.stop_loss,
                "rr_ratio": s.rr_ratio,
                "timeframe": s.timeframe,
                "timestamp": s.timestamp,
                "source": s.source,
            }
            for s in signals
        ],
        "count": len(signals),
    }


@app.get("/api/backtest/symbols")
async def backtest_symbols() -> dict[str, Any]:
    reader = SignalReader(PGV2_TRADING_DB, PGV2_ARCHIVE_DB)
    symbols = reader.symbols()
    return {"items": symbols, "count": len(symbols)}


@app.get("/api/backtest/variants")
async def backtest_variants() -> dict[str, Any]:
    from level_tester.backtester.db_variant import DB_VARIANT_ID, DB_VARIANT_NAME

    items = [
        {
            "id": DB_VARIANT_ID,
            "name": DB_VARIANT_NAME,
            "db_baseline": True,
            "sl_pct": None,
            "tp_rr": None,
            "tp_pct": None,
            "trailing_activation_pct": None,
            "trailing_stop_pct": None,
            "trailing_update_threshold_pct": None,
            "trailing_tp_only": False,
            "breakeven_trigger_pct": None,
            "breakeven_lock_pct": None,
            "partial_close_pct": None,
            "partial_close_rr": None,
            "trailing": None,
            "breakeven": None,
            "partial": None,
        }
    ]
    items.extend(
        [
            {
                "id": v.id,
                "name": v.name,
                "db_baseline": False,
                "sl_pct": str(v.sl_pct),
                "tp_rr": str(v.tp_rr) if v.tp_rr else None,
                "tp_pct": str(v.tp_pct) if v.tp_pct else None,
                "trailing_activation_pct": str(v.trailing_activation_pct) if v.trailing_activation_pct is not None else None,
                "trailing_stop_pct": str(v.trailing_stop_pct) if v.trailing_stop_pct is not None else None,
                "trailing_update_threshold_pct": str(v.trailing_update_threshold_pct) if v.trailing_update_threshold_pct is not None else None,
                "trailing_tp_only": v.trailing_tp_only,
                "breakeven_trigger_pct": str(v.breakeven_trigger_pct) if v.breakeven_trigger_pct is not None else None,
                "breakeven_lock_pct": str(v.breakeven_lock_pct) if v.breakeven_lock_pct is not None else None,
                "partial_close_pct": str(v.partial_close_pct) if v.partial_close_pct is not None else None,
                "partial_close_rr": str(v.partial_close_rr) if v.partial_close_rr is not None else None,
                "trailing": v.trailing_activation_pct is not None,
                "breakeven": v.breakeven_trigger_pct is not None,
                "partial": v.partial_close_pct is not None,
            }
            for v in BUILTIN_VARIANTS
        ]
    )
    return {"items": items, "count": len(items)}


@app.get("/api/backtest/db_preview")
async def backtest_db_preview(signal_ids: str = Query(default="", description="comma-separated signal IDs")) -> dict[str, Any]:
    """Preview per-signal DB baseline resolution (params + source tags)."""
    from level_tester.backtester.db_variant import resolve_db_variant

    ids = [s.strip() for s in signal_ids.split(",") if s.strip()]
    if not ids:
        return {"items": []}
    reader = SignalReader(PGV2_TRADING_DB, PGV2_ARCHIVE_DB)
    items = []
    for sid in ids[:50]:
        found = reader.read(signal_id=sid, include_trading=True, include_archive=True, limit=1)
        if not found:
            items.append({"signal_id": sid, "error": "not found"})
            continue
        sig = found[0]
        resolved = resolve_db_variant(sig)
        v = resolved.variant
        items.append({
            "signal_id": sid,
            "symbol": sig.symbol,
            "side": sig.side,
            "entry_price": resolved.entry_price,
            "sl_price": resolved.sl_price,
            "tp_price": resolved.tp_price,
            "sl_pct": str(v.sl_pct),
            "tp_rr": str(v.tp_rr) if v.tp_rr is not None else None,
            "tp_pct": str(v.tp_pct) if v.tp_pct is not None else None,
            "trailing_activation_pct": str(v.trailing_activation_pct) if v.trailing_activation_pct is not None else None,
            "trailing_stop_pct": str(v.trailing_stop_pct) if v.trailing_stop_pct is not None else None,
            "trailing_update_threshold_pct": str(v.trailing_update_threshold_pct) if v.trailing_update_threshold_pct is not None else None,
            "trailing_tp_only": v.trailing_tp_only,
            "breakeven_trigger_pct": str(v.breakeven_trigger_pct) if v.breakeven_trigger_pct is not None else None,
            "breakeven_lock_pct": str(v.breakeven_lock_pct) if v.breakeven_lock_pct is not None else None,
            "partial_close_pct": str(v.partial_close_pct) if v.partial_close_pct is not None else None,
            "partial_close_rr": str(v.partial_close_rr) if v.partial_close_rr is not None else None,
            "sources": resolved.sources,
        })
    return {"items": items, "count": len(items)}


@app.post("/api/backtest/run", status_code=202)
async def backtest_run(request: BacktestRunRequest) -> dict[str, Any]:
    global _backtest_counter
    _backtest_counter += 1
    job_id = f"bt_{_backtest_counter}"

    _backtest_jobs[job_id] = {
        "status": "pending",
        "progress": 0,
        "total": len(request.signal_ids),
        "current_symbol": "",
        "results": None,
        "error": None,
    }

    Thread(
        target=_run_backtest,
        args=(job_id, request),
        daemon=True,
    ).start()

    return {"job_id": job_id, "status": "pending"}


@app.get("/api/backtest/status/{job_id}")
async def backtest_status(job_id: str) -> dict[str, Any]:
    job = _backtest_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.get("/api/backtest/report/{job_id}")
async def backtest_report(job_id: str) -> dict[str, Any]:
    job = _backtest_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] != "completed":
        raise HTTPException(status_code=409, detail="job not completed yet")
    return job.get("results", {})


def _run_backtest(job_id: str, request: BacktestRunRequest) -> None:
    job = _backtest_jobs[job_id]
    try:
        job["status"] = "running"

        reader = SignalReader(PGV2_TRADING_DB, PGV2_ARCHIVE_DB)
        all_signals = []
        for sid in request.signal_ids:
            found = reader.read(signal_id=sid, include_archive=True, limit=1)
            all_signals.extend(found)

        if not all_signals:
            job["status"] = "failed"
            job["error"] = "no signals found for given IDs"
            return

        # Build variants: custom payloads override builtin by id; selected ids must exist
        from level_tester.backtester.db_variant import DB_VARIANT_ID, DB_VARIANT_NAME
        from level_tester.backtester.variants import Variant

        custom_by_id: dict[str, Variant] = {}
        for p in request.custom_variants:
            custom_by_id[p.id] = Variant(
                id=p.id,
                name=p.name,
                sl_pct=p.sl_pct,
                tp_rr=p.tp_rr,
                tp_pct=p.tp_pct,
                trailing_activation_pct=p.trailing_activation_pct,
                trailing_stop_pct=p.trailing_stop_pct,
                trailing_update_threshold_pct=p.trailing_update_threshold_pct,
                trailing_tp_only=p.trailing_tp_only,
                breakeven_trigger_pct=p.breakeven_trigger_pct,
                breakeven_lock_pct=p.breakeven_lock_pct,
                partial_close_pct=p.partial_close_pct,
                partial_close_rr=p.partial_close_rr,
            )
        builtin_by_id = {v.id: v for v in get_builtin()}
        variants: list[Variant] = []
        for vid in request.variants:
            if vid == DB_VARIANT_ID:
                from decimal import Decimal as _Dec
                # Placeholder: real params resolved per-signal in the engine
                variants.append(Variant(id=DB_VARIANT_ID, name=DB_VARIANT_NAME, sl_pct=_Dec("1")))
            elif vid in custom_by_id:
                variants.append(custom_by_id[vid])
            elif vid in builtin_by_id:
                variants.append(builtin_by_id[vid])
            else:
                job["status"] = "failed"
                job["error"] = f"unknown variant id: {vid}"
                return
        if not variants:
            job["status"] = "failed"
            job["error"] = "no variants selected"
            return
        entry_type = EntryType(request.entry_type)

        client = BinanceFuturesClient()
        engine = BacktestEngine(client)

        METHOD_LABELS = {0: "Touch", 1: "1 bar", 2: "2 bars"}
        methods = request.confirmation_methods or [2]
        total_signals = len(all_signals) * len(methods)

        def progress(current, total, symbol):
            job["progress"] = current
            job["total"] = total
            job["current_symbol"] = symbol

        all_results = []
        for method in methods:
            req_bars = method  # 0=touch, 1=1bar, 2=2bars
            method_label = METHOD_LABELS.get(method, f"{method} bars")
            results = engine.run_all(
                all_signals, variants, entry_type,
                lookback_bars=request.lookback,
                lookforward_bars=request.lookforward,
                limit_offset_pct=request.limit_offset,
                confirmation_bars=req_bars,
                confirmation_max_wait=15,  # default max_wait for confirmation phase
                progress_callback=progress,
            )
            # Tag each result with the method for grouping
            for r in results:
                r.method = method
                r.method_label = method_label
            all_results.extend(results)

        by_variant: dict[str, list] = {}
        for r in all_results:
            key = f"{r.variant.id}|{r.method}"
            by_variant.setdefault(key, []).append((r, r.method_label))

        metrics = compute_all_metrics_by_key(by_variant)

        job["status"] = "completed"
        job["progress"] = job["total"]
        job["results"] = {
            "metrics": [
                {
                    "variant_id": m.variant_id,
                    "variant_name": m.variant_name,
                    "total_trades": m.total_trades,
                    "wins": m.wins,
                    "losses": m.losses,
                    "winrate": round(m.winrate, 1),
                    "total_pnl": float(m.total_pnl),
                    "total_pnl_pct": float(m.total_pnl_pct),
                    "avg_pnl": float(m.avg_pnl),
                    "avg_pnl_pct": float(m.avg_pnl_pct),
                    "avg_win": float(m.avg_win),
                    "avg_win_pct": float(m.avg_win_pct),
                    "avg_loss": float(m.avg_loss),
                    "avg_loss_pct": float(m.avg_loss_pct),
                    "profit_factor": round(m.profit_factor, 2) if m.profit_factor != float("inf") else "Infinity",
                    "max_drawdown": float(m.max_drawdown),
                    "expectancy": float(m.expectancy),
                    "expectancy_pct": float(m.expectancy_pct),
                    "avg_bars_held": round(m.avg_bars_held, 1),
                    "long_trades": m.long_trades,
                    "short_trades": m.short_trades,
                    "long_winrate": round(m.long_winrate, 1),
                    "short_winrate": round(m.short_winrate, 1),
                    "no_entry": m.no_entry,
                    "equity_curve": m.equity_curve,
                    "equity_curve_pct": m.equity_curve_pct,
                }
                for m in metrics
            ],
            "trades": [
                {
                    "signal_id": r.signal.signal_id,
                    "symbol": r.signal.symbol,
                    "side": r.trade.side,
                    "entry_price": str(r.trade.entry_price),
                    "entry_time": r.trade.entry_time.isoformat() if r.trade.entry_time else None,
                    "exit_price": str(r.trade.exit_price) if r.trade.exit_price else None,
                    "exit_time": r.trade.exit_time.isoformat() if r.trade.exit_time else None,
                    "exit_reason": r.trade.exit_reason,
                    "pnl": float(r.trade.pnl) if r.trade.pnl is not None else 0,
                    "pnl_pct": float(r.trade.pnl_pct) if r.trade.pnl_pct is not None else 0,
                    "bars_held": r.trade.bars_held,
                    "stop_price": str(r.trade.stop_price),
                    "take_price": str(r.trade.take_price),
                    "variant_id": r.variant.id,
                    "variant_name": r.variant.name,
                    "confirmation_method": r.method,
                    "trailing_stop_pct": str(r.variant.trailing_stop_pct) if r.variant.trailing_stop_pct is not None else None,
                    "trailing_activation_pct": str(r.variant.trailing_activation_pct) if r.variant.trailing_activation_pct is not None else None,
                    "trailing_update_threshold_pct": str(r.variant.trailing_update_threshold_pct) if r.variant.trailing_update_threshold_pct is not None else None,
                    "trailing_tp_only": r.variant.trailing_tp_only,
                    "breakeven_trigger_pct": str(r.variant.breakeven_trigger_pct) if r.variant.breakeven_trigger_pct is not None else None,
                    "breakeven_lock_pct": str(r.variant.breakeven_lock_pct) if r.variant.breakeven_lock_pct is not None else None,
                    "partial_close_pct": str(r.variant.partial_close_pct) if r.variant.partial_close_pct is not None else None,
                    "partial_close_rr": str(r.variant.partial_close_rr) if r.variant.partial_close_rr is not None else None,
                }
                for r in all_results
            ],
            "signals_count": len(all_signals),
            "entry_type": request.entry_type,
        }

    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)[:500]


def _load_run(run_id: str, seed: list[Candle] | None) -> None:
    run = service.get(run_id)
    try:
        service.update_loading(run_id, 0, "searching instrument")
        _persist_run(run)
        with session_factory() as session:
            instrument = instrument_repository.find(session, run.symbol)
            if instrument is None and seed is not None:
                instrument = InstrumentRow(
                    symbol=run.symbol,
                    exchange=default_config.market.exchange,
                    market_type=default_config.market.market_type,
                    quote_asset=default_config.market.quote_asset,
                    base_asset=run.symbol.removesuffix(default_config.market.quote_asset),
                    status="TRADING",
                )
                session.add(instrument)
                session.commit()
            if instrument is None:
                instrument_service.sync(session)
                instrument = instrument_repository.find(session, run.symbol)
            if instrument is None:
                raise RuntimeError(f"instrument {run.symbol} was not found on Binance")
            service.update_loading(run_id, 10, "loading candles")
            _persist_run(run)
            if seed is not None:
                if any(candle.timeframe != "1h" for candle in seed):
                    raise ValueError("seed_candles must contain 1h candles")
                candle_repository.upsert_many(session, instrument.id, seed)
            else:
                coverage = ingestion_service.ensure_range(
                    session,
                    instrument.id,
                    run.symbol,
                    "1h",
                    run.window.calculation_from,
                    run.window.effective_to,
                    lambda progress: _set_progress(run_id, progress, "loading candles"),
                )
                if coverage.missing_count:
                    raise RuntimeError(
                        f"H1 data has {coverage.missing_count} missing candles after ingestion"
                    )
            service.update_loading(run_id, 90, "validating data")
            _persist_run(run)
            candles = candle_repository.list_range(
                session,
                instrument.id,
                "1h",
                run.window.calculation_from,
                run.window.effective_to + timedelta(microseconds=1),
            )
            if not candles:
                raise RuntimeError("no H1 candles were loaded")
        service.update_loading(run_id, 95, "initializing engine")
        _persist_run(run)
        instrument_tick = instrument.tick_size or Decimal("0.01")
        instrument_precision = (
            instrument.price_precision
            if instrument.price_precision
            else price_precision_from_tick(instrument_tick)
        )
        config = replace(
            default_config,
            level=replace(default_config.level, tick_size=instrument_tick),
            detail_timeframe=run.detail_timeframe,
            confirmation=replace(
                default_config.confirmation,
                method=run.confirmation_methods[0],
                timeframe=run.detail_timeframe,
                required_bars=run.confirmation_required_bars,
                max_wait_bars=run.confirmation_max_wait_bars,
            ),
            confirmation_methods=run.confirmation_methods,
        )
        service.complete_loading(
            run_id,
            candles,
            None,
            config,
            instrument.id,
            tick_size=instrument_tick,
            price_precision=instrument_precision,
        )
        _persist_run(service.get(run_id))
    except Exception as exc:  # noqa: BLE001 - loading boundary stores failure on the run
        failed = service.fail_loading(run_id, str(exc))
        _persist_run(failed)


def _sync_catalog() -> int:
    with session_factory() as session:
        return instrument_service.sync(session)


def _set_progress(run_id: str, progress: int, stage: str = "") -> None:
    run = service.update_loading(run_id, progress, stage)
    _persist_run(run)


def _persist_run(run) -> None:
    try:
        cursor = run.engine.cursor.bar_time if run.engine and run.engine.cursor else None
        with session_factory() as session:
            run_repository.save_run(
                session,
                run.id,
                run.symbol,
                run.window.display_from,
                run.window.calculation_from,
                run.window.effective_to,
                run.status.value,
                run.config_hash,
                {
                    "detail_timeframe": run.detail_timeframe,
                    "confirmation_methods": list(run.confirmation_methods),
                    "confirmation_required_bars": run.confirmation_required_bars,
                    "confirmation_max_wait_bars": run.confirmation_max_wait_bars,
                },
                run.data_set_id,
                run.progress,
                run.error_message,
                cursor,
            )
            if run.engine and run.engine.cursor:
                run_repository.save_checkpoint(
                    session,
                    run.id,
                    run.engine.cursor.sequence,
                    run.engine.cursor.bar_time,
                    run.engine.checkpoint(),
                )
    except Exception:  # noqa: BLE001 - persistence must not hide the in-memory run
        # The in-memory state remains available; /api/status exposes DB failure.
        return


def _run_or_404(run_id: str):
    try:
        return service.get(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _snapshot_or_404(run_id: str) -> dict[str, Any]:
    try:
        return service.snapshot(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _instrument_json(row: InstrumentRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "symbol": row.symbol,
        "base_asset": row.base_asset,
        "quote_asset": row.quote_asset,
        "status": row.status,
        "daily_volume_usdt": str(row.daily_volume),
        "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
    }


def _coverage_json(coverage) -> dict[str, Any]:
    return {
        "timeframe": coverage.timeframe,
        "required_from": coverage.required_from.isoformat(),
        "required_to": coverage.required_to.isoformat(),
        "required_count": coverage.required_count,
        "stored_count": coverage.stored_count,
        "missing_count": coverage.missing_count,
        "missing_ranges": [
            {"from": start.isoformat(), "to": end.isoformat()}
            for start, end in coverage.missing_ranges
        ],
    }


def _validate_range(start: datetime, end: datetime) -> None:
    _utc(start)
    _utc(end)
    if start >= end:
        raise HTTPException(status_code=422, detail="start must be before end")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(status_code=422, detail="timestamps must include a timezone")
    return value.astimezone(UTC)


def _last_closed_boundary() -> datetime:
    now = datetime.now(UTC)
    return now.replace(minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# HourBounce Review Lite: один сигнал из архива PGv2 -> реплей T1/T2/T3 x SL
# ---------------------------------------------------------------------------
def _hb_parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            # PGv2 пишет naive-метки в UTC (см. exchange_state_manager: replace(tzinfo=utc))
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (ValueError, TypeError):
        return None


_HB_ID_TS_RE = re.compile(r"^binance_.+_(?:resistance|support)_[\d.]+_(\d{8}_\d{6})$")


def _hb_dt_place(signal_id: str | None, placement_date: str | None, created_at: str | None,
                 watch_start: str | None = None) -> tuple[str | None, datetime | None]:
    """Время выставления уровня (UTC) + якорь сканирования.

    dt_place: метка в id (детект H1-уровня, UTC) -> placement_date -> created_at.
    Якорь сканирования: max(dt_place, watch_start) — PGv2 не видел свечей до
    старта своего confirmation-цикла (рестарты бота), реплей 1:1 тоже не должен.
    Возвращает (строка для UI, aware-datetime якоря).
    """
    m = _HB_ID_TS_RE.match(str(signal_id or ""))
    place: datetime | None = None
    if m:
        try:
            place = datetime.strptime(m.group(1), "%Y%m%d_%H%M%S").replace(tzinfo=UTC)
        except ValueError:
            place = None
    if place is None:
        for raw in (placement_date, created_at):
            place = _hb_parse_time(raw)
            if place is not None:
                break
    if place is None:
        return None, None
    anchor = place
    ws = _hb_parse_time(watch_start)
    if ws is not None and ws > anchor:
        anchor = ws
    return place.strftime("%Y-%m-%d %H:%M:%S"), anchor


def _hb_archive_meta(signal_id: str) -> dict[str, Any]:
    """Одна строка metadata архива: placement_date и эталон касания PGv2."""
    try:
        adb = Path(str(PGV2_ARCHIVE_DB))
        if not adb.exists():
            return {}
        import json as _json
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(f"file:{adb.as_posix()}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT metadata FROM signal_archive WHERE original_id = ? OR id = ? LIMIT 1",
                (signal_id, signal_id),
            ).fetchone()
            if not row or not row[0]:
                return {}
            meta = _json.loads(row[0]) if isinstance(row[0], str) else {}
            if not isinstance(meta, dict):
                return {}
            try:
                req = int(meta.get("confirmation_bars_required", 2))
            except (TypeError, ValueError):
                req = 2
            return {
                "placement_date": meta.get("placement_date") or meta.get("timestamp") or meta.get("load_date"),
                # эталон PGv2: когда стартовал подсчёт баров после касания (UTC ISO)
                "touch_ref": meta.get("confirmation_started_at"),
                "required_bars": req,
                "watch_start": meta.get("confirmation_waiting_started_at"),
                # когда PGv2 реально начал смотреть уровень (рестарт бота сдвигает);
                # сканируем от max(dt_place, watch_start), иначе находим касания,
                # которых живой бот не видел
                "watch_start": meta.get("confirmation_waiting_started_at"),
            }
        finally:
            conn.close()
    except Exception:
        return {}


def _hb_arch_outcome(status: str | None, close_reason: str | None) -> str:
    s = (status or "").lower()
    r = (close_reason or "").lower()
    if "tp" in s or "tp_filled" in r or "take" in r:
        return "TAKE"
    if "sl" in s or "stop" in r or "sl_" in r:
        return "STOP"
    if "cancel" in s or "timeout" in r or "deviation" in r or "reject" in r:
        return "NO_ENTRY"
    return "EXPIRED"


def _hb_price_spec(symbol: str, fallback_price: float | str) -> dict[str, Any]:
    """Биржевая точность цены: tick_size/price_precision из каталога, иначе по знакам цены."""
    try:
        with session_factory() as session:
            row = instrument_repository.find(session, symbol)
            if row is not None and row.tick_size:
                tick = Decimal(str(row.tick_size))
                prec = int(row.price_precision) if row.price_precision is not None else None
                if prec is None:
                    prec = price_precision_from_tick(tick)
                return {"price_precision": prec, "tick_size": str(tick)}
    except Exception:
        pass
    try:
        exp = Decimal(str(fallback_price)).normalize().as_tuple().exponent
        prec = max(0, min(8, -exp))
    except Exception:
        prec = 4
    return {"price_precision": prec, "tick_size": None}


def _hb_signal_json(s) -> dict[str, Any]:
    import json as _json

    meta: dict[str, Any] = {}
    try:
        # SignalReader не отдаёт metadata наружу — перечитаем её здесь для dt_place
        pass
    except Exception:
        pass
    ts = _hb_parse_time(s.timestamp)
    return {
        "signal_id": s.signal_id,
        "symbol": s.symbol,
        "side": "LONG" if str(s.side).upper() in ("LONG", "BUY") else "SHORT",
        "side_raw": s.side,
        "level_price": s.entry_price,
        "dt_place": s.timestamp,
        "dt_place_ts": int(ts.timestamp()) if ts else None,
        "daily_volume": None,  # в архиве нет — прочерк по решению заказчика
        "natr": None,  # в архиве нет — прочерк
        "tp_arch": s.take_profits,
        "sl_arch": s.stop_loss,
        "rr_arch": s.rr_ratio,
        "source": s.source,
    }


@app.get("/api/hourbounce/signals")
async def hourbounce_signals(
    symbol: str = "",
    side: str = "",
    period: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    search: str = "",
    date_from: str = Query(default="", description="YYYY-MM-DD: сигналы с этой даты выставления (UTC)"),
    date_to: str = Query(default="", description="YYYY-MM-DD: сигналы по эту дату выставления (UTC)"),
    sort: str = Query(default="date_desc",
                       description="date_desc|date_asc|name_asc|name_desc|symbol_asc"),
) -> dict[str, Any]:
    import sqlite3 as _sqlite3
    from datetime import date as _date

    reader = SignalReader(PGV2_TRADING_DB, PGV2_ARCHIVE_DB)
    period_start, period_end = (period.split(":") if ":" in period else (None, None))
    # архив хранит LONG/SHORT — так и показываем, без маппинга в BUY/SELL
    side_norm = side.upper() or None
    signals = reader.read(
        symbol=symbol or None,
        side=None,  # фильтр ниже по точному совпадению LONG/SHORT/ALL
        period_start=period_start,
        period_end=period_end,
        limit=500,
        include_trading=False,
        include_archive=True,
    )
    # архивный outcome/status/close_reason + dt_place из metadata — одним запросом
    arch_extra: dict[str, dict[str, Any]] = {}
    try:
        adb = Path(str(PGV2_ARCHIVE_DB))
        if adb.exists():
            conn = _sqlite3.connect(f"file:{adb.as_posix()}?mode=ro", uri=True)
            conn.row_factory = _sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT original_id, id, status, close_reason, created_at, metadata FROM signal_archive"
                ).fetchall()
                for r in rows:
                    keys = set(r.keys())
                    meta_raw = r["metadata"] if "metadata" in keys else None
                    placement = None
                    touch_ref = None
                    watch_start = None
                    try:
                        import json as _json

                        m = _json.loads(meta_raw) if isinstance(meta_raw, str) and meta_raw else {}
                        placement = m.get("placement_date") or m.get("timestamp") or m.get("load_date")
                        touch_ref = m.get("confirmation_started_at")
                        watch_start = m.get("confirmation_waiting_started_at")
                    except Exception:
                        placement = None
                    for k in (r["original_id"], r["id"]):
                        if k:
                            arch_extra[str(k)] = {
                                "status": r["status"],
                                "close_reason": r["close_reason"],
                                "placement_date": placement,
                                "touch_ref": touch_ref,
                                "watch_start": watch_start,
                            }
                    # ключ original_id может быть без суффикса, а SignalReader отдаёт original_id как есть
            finally:
                conn.close()
    except Exception:
        arch_extra = {}

    items: list[dict[str, Any]] = []
    for s in signals:
        hb_side = "LONG" if str(s.side).upper() in ("LONG", "BUY") else "SHORT"
        if side and side.upper() not in ("ALL", hb_side):
            continue
        if search and search.lower() not in (f"{s.symbol} {s.signal_id}".lower()):
            continue
        extra = arch_extra.get(str(s.signal_id), {})
        outcome_arch = _hb_arch_outcome(extra.get("status"), extra.get("close_reason"))
        dt_place, dt_place_dt = _hb_dt_place(s.signal_id, extra.get("placement_date"), s.timestamp,
                                             extra.get("watch_start"))
        place_day = dt_place_dt.date().isoformat() if dt_place_dt else None
        if date_from:
            try:
                if place_day is None or place_day < _date.fromisoformat(date_from).isoformat():
                    continue
            except ValueError:
                pass
        if date_to:
            try:
                if place_day is None or place_day > _date.fromisoformat(date_to).isoformat():
                    continue
            except ValueError:
                pass
        items.append({
            "signal_id": s.signal_id,
            "symbol": s.symbol,
            "side": hb_side,
            "level_price": s.entry_price,
            "dt_place": dt_place,
            "dt_place_ts": int(dt_place_dt.timestamp()) if dt_place_dt else None,
            "created_at": s.timestamp,
            "touch_ref": extra.get("touch_ref"),
            "daily_volume": None,
            "natr": None,
            "tp_arch": s.take_profits,
            "sl_arch": s.stop_loss,
            "rr_arch": s.rr_ratio,
            "outcome_arch": outcome_arch,
            "status": extra.get("status"),
            "close_reason": extra.get("close_reason"),
            "pg_available": s.stop_loss is not None,
            "source": s.source,
        })
    if sort == "date_asc":
        items.sort(key=lambda d: d["dt_place"] or "")
    elif sort == "name_asc":
        items.sort(key=lambda d: (d["signal_id"] or "").lower())
    elif sort == "name_desc":
        items.sort(key=lambda d: (d["signal_id"] or "").lower(), reverse=True)
    elif sort == "symbol_asc":
        items.sort(key=lambda d: ((d["symbol"] or ""), (d["dt_place"] or "")))
    else:  # date_desc
        items.sort(key=lambda d: d["dt_place"] or "", reverse=True)
    return {"items": items[:limit], "count": len(items)}


def _hb_resolve_signal(signal_id: str) -> tuple[Any, dict[str, Any], str | None, datetime, str, dict[str, Any]]:
    """Сигнал архива + dt_place UTC + side LONG/SHORT + точность цены."""
    reader = SignalReader(PGV2_TRADING_DB, PGV2_ARCHIVE_DB)
    found = reader.read(signal_id=signal_id, include_trading=False, include_archive=True, limit=1)
    if not found:
        raise HTTPException(status_code=404, detail="signal not found in archive")
    sig = found[0]
    meta = _hb_archive_meta(signal_id)
    dt_place_str, sig_time = _hb_dt_place(signal_id, meta.get("placement_date"), sig.timestamp,
                                          meta.get("watch_start"))
    if sig_time is None:
        raise HTTPException(status_code=422, detail="signal has no valid timestamp")
    side = "LONG" if str(sig.side).upper() in ("LONG", "BUY") else "SHORT"
    price_spec = _hb_price_spec(sig.symbol, sig.entry_price)
    return sig, meta, dt_place_str, sig_time, side, price_spec


def _hb_signal_block(sig, meta, dt_place_str, sig_time, side, price_spec) -> dict[str, Any]:
    try:
        place_ts = int(datetime.strptime(str(dt_place_str), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).timestamp())
    except (ValueError, TypeError):
        place_ts = int(sig_time.timestamp())
    return {
        "signal_id": sig.signal_id, "symbol": sig.symbol, "side": side,
        "level_price": sig.entry_price, "dt_place": dt_place_str,
        "place_ts": place_ts, "place_dt_iso": sig_time.isoformat(),
        "created_at": sig.timestamp, "touch_ref": meta.get("touch_ref"),
        "tp_arch": sig.take_profits, "sl_arch": sig.stop_loss, "rr_arch": sig.rr_ratio,
        "price_precision": price_spec["price_precision"],
        "tick_size": price_spec["tick_size"],
    }


def _hb_build_matrix(signal_id: str, lookforward: int, pre: int, post: int,
                     refresh: bool = False) -> dict[str, Any]:
    """Матрица 9 ячеек HourBounce (сетка SL).

    Свечи: сначала БД (кеш 5m), дыры догружаются с Binance идемпотентно.
    Ячейки: кеш hb_reviews по (signal_id, config_fp, lookforward); пересчёт —
    при промахе, refresh=1 или когда догрузились новые свечи (закрыли дыры).
    Срезы lo/hi считаются под запрошенные pre/post при каждом вызове.
    """
    from level_tester.backtester.hourbounce import DEFAULT_CONFIG, review_matrix
    from level_tester.infrastructure.hourbounce_store import (
        get_review_payload, load_candles_cached, put_review_payload,
    )

    sig, meta, dt_place_str, sig_time, side, price_spec = _hb_resolve_signal(signal_id)

    start = sig_time - pre * timedelta(minutes=5)
    # формирующаяся свеча меняется при каждом запросе — режем конец по последней закрытой M5
    _now = datetime.now(UTC)
    now_floor = _now.replace(minute=(_now.minute // 5) * 5, second=0, microsecond=0)
    end = min(sig_time + lookforward * timedelta(minutes=5), now_floor)
    try:
        candles, cache_stats = load_candles_cached(
            session_factory, binance_client, sig.symbol, start, end, refresh=refresh)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"candle load failed: {exc}") from exc
    if not candles:
        raise HTTPException(status_code=502, detail="no candles returned")

    is_long = side == "LONG"
    lvl = Decimal(str(sig.entry_price))
    eng_window = [c for c in candles if c.open_time >= sig_time][: DEFAULT_CONFIG.life_window_t]
    window_end = eng_window[-1].close_time if eng_window else None
    window_last = eng_window[-1].open_time.isoformat() if eng_window else None
    window_count = len(eng_window)
    touches_outside = sum(
        1 for c in candles
        if (window_end is not None and c.open_time >= window_end)
        and ((c.low <= lvl) if is_long else (c.high >= lvl))
    )

    signal_block = _hb_signal_block(sig, meta, dt_place_str, sig_time, side, price_spec)

    cells: list[dict[str, Any]] | None = None
    if not refresh:
        try:
            with session_factory() as session:
                hit = get_review_payload(session, signal_id, lookforward)
        except Exception:
            hit = None
        if hit and isinstance(hit.get("cells"), list) and len(hit["cells"]) == 9:
            # хвост за окном жизни на итог не влияет — сверяем границу и размер
            # окна: тогда каждая новая M5-свеча кеш не инвалидирует, а закрытые
            # дыры внутри окна (счётчик/граница изменились) — инвалидируют
            if hit.get("window_last") == window_last and hit.get("window_count") == window_count:
                cells = hit["cells"]
    cells_hit = cells is not None
    if cells is None:
        results = review_matrix(
            side=side, level_price=Decimal(str(sig.entry_price)),
            signal_time=sig_time, candles=candles, config=DEFAULT_CONFIG,
        )
        cells = []
        for res in results:
            j = _hb_result_json(res)
            j["sl_pct"] = str(DEFAULT_CONFIG.sl_sizes[res.sl_index - 1])
            cells.append(j)
        try:
            with session_factory() as session:
                put_review_payload(
                    session, signal_id, sig.symbol, lookforward,
                    {"signal": signal_block, "cells": cells,
                     "window_end": window_end.isoformat() if window_end else None,
                     "window_last": window_last, "window_count": window_count,
                     "touches_outside": touches_outside},
                    candles[0].open_time, candles[-1].close_time)
        except Exception:
            pass

    by_open = {c.open_time: i for i, c in enumerate(candles)}
    try:
        sig_idx = next(i for i, c in enumerate(candles) if c.open_time >= sig_time)
    except StopIteration:
        sig_idx = 0
    out_cells = []
    for cell in cells:
        c2 = dict(cell)
        anchor = None
        if cell.get("touch_dt"):
            tdt = _hb_parse_time(cell["touch_dt"])
            anchor = by_open.get(tdt, None) if tdt else None
        if anchor is None:
            anchor = sig_idx
        end_idx = len(candles) - 1
        if cell.get("exit_dt"):
            edt = _hb_parse_time(cell["exit_dt"])
            if edt:
                for i, c in enumerate(candles):
                    if c.close_time >= edt or c.open_time >= edt:
                        end_idx = i
                        break
        c2["lo"] = max(0, anchor - pre)
        c2["hi"] = min(len(candles), end_idx + 1 + post)
        out_cells.append(c2)

    def _c(c) -> dict[str, Any]:
        return {
            "time": int(c.open_time.timestamp()),
            "open": float(c.open), "high": float(c.high),
            "low": float(c.low), "close": float(c.close),
            "volume": float(c.volume),
        }

    return {
        "signal": signal_block,
        "sl_sizes": [str(s) for s in DEFAULT_CONFIG.sl_sizes],
        "window_end": window_end.isoformat() if window_end else None,
        "touches_outside": touches_outside,
        "sig_idx": sig_idx,
        "cells": out_cells,
        "candles": [_c(c) for c in candles],
        "cache": {
            "candles_cached": int(cache_stats.get("from_cache") or 0),
            "candles_loaded": int(cache_stats.get("from_binance") or 0),
            "cells_hit": cells_hit,
        },
    }


def _hb_build_single(signal_id: str, entry: str, lookforward: int, pre: int,
                     refresh: bool = False) -> dict[str, Any]:
    """Одна ячейка в режиме сделки PGv2 1:1: подтверждает required_bars подряд
    СЧИТАЯ свечу касания (как confirmation_loop.py), вход по open следующей,
    SL/TP/BE/trailing из архива. Свечи — из кеша БД. Результат не кешируем.
    """
    from level_tester.backtester.hourbounce import DEFAULT_CONFIG, exec_from_signal, review_signal
    from level_tester.infrastructure.hourbounce_store import load_candles_cached

    sig, meta, dt_place_str, sig_time, side, price_spec = _hb_resolve_signal(signal_id)
    ex = exec_from_signal(sig)
    if ex is None:
        return {"pg_available": False,
                "pg_reason": "в архиве нет stop_loss — режим сделки недоступен",
                "signal": _hb_signal_block(sig, meta, dt_place_str, sig_time, side, price_spec)}
    start = sig_time - pre * timedelta(minutes=5)
    _now = datetime.now(UTC)
    now_floor = _now.replace(minute=(_now.minute // 5) * 5, second=0, microsecond=0)
    end = min(sig_time + lookforward * timedelta(minutes=5), now_floor)
    try:
        candles, cache_stats = load_candles_cached(
            session_factory, binance_client, sig.symbol, start, end, refresh=refresh)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"candle load failed: {exc}") from exc
    if not candles:
        raise HTTPException(status_code=502, detail="no candles returned")

    res = review_signal(
        side=side, level_price=Decimal(str(sig.entry_price)), signal_time=sig_time,
        candles=candles, entry_code="PG", sl_index=0,
        config=DEFAULT_CONFIG, exec_params=ex,
        pg_required=int(meta.get("required_bars", 2)),
    )
    all_ts = [c.open_time for c in candles]
    try:
        sig_idx = next(i for i, c in enumerate(candles) if c.open_time >= sig_time)
    except StopIteration:
        sig_idx = 0
    if res.touch_dt and res.touch_dt in all_ts:
        anchor = all_ts.index(res.touch_dt)
        mode = "touch"
        end_idx = len(candles) - 1
        if res.exit_dt is not None:
            for i, c in enumerate(candles):
                if c.close_time >= res.exit_dt or c.open_time >= res.exit_dt:
                    end_idx = i
                    break
        lo, hi = max(0, anchor - pre), min(len(candles), end_idx + 1 + 30)
    else:
        lo, hi, mode = max(0, sig_idx - pre), min(len(candles), sig_idx + 96), "placement"

    def _c(c) -> dict[str, Any]:
        return {
            "time": int(c.open_time.timestamp()),
            "open": float(c.open), "high": float(c.high),
            "low": float(c.low), "close": float(c.close),
            "volume": float(c.volume),
        }

    result = _hb_result_json(res)
    result["mode"] = mode
    return {
        "pg_available": True, "pg_reason": None,
        "exec": {
            "required_bars": int(meta.get("required_bars", 2)),
            "sl": str(ex.sl_price),
            "sl_how": ex.sl_source,
            "tp": str(ex.fixed_tp) if ex.fixed_tp is not None else None,
            "be": f"{ex.be_trigger_pct}/{ex.be_lock_pct}" if ex.use_be else None,
            "trail": f"{ex.trail_activation_pct}/{ex.trail_distance_pct}" if ex.use_trail else None,
            "trail_tp_only": ex.trail_tp_only,
        },
        "signal": _hb_signal_block(sig, meta, dt_place_str, sig_time, side, price_spec),
        "params": {"entry": entry, "sl_index": 0, "sl_pct": None, "mode": "signal"},
        "result": result,
        "candles": [_c(c) for c in candles[lo:hi]],
        "cache": {
            "candles_cached": int(cache_stats.get("from_cache") or 0),
            "candles_loaded": int(cache_stats.get("from_binance") or 0),
            "cells_hit": False,
        },
    }


@app.get("/api/hourbounce/review")
async def hourbounce_review(
    signal_id: str = Query(min_length=1),
    entry: str = Query(default="T1", pattern=r"^(T1|T2|T3)$"),
    sl: int = Query(default=1, ge=1, le=3),
    lookforward: int = Query(default=2000, ge=20, le=20000),
    pre: int = Query(default=30, ge=0, le=200),
    post: int = Query(default=30, ge=0, le=200),
    refresh: bool = Query(default=False),
    mode: str = Query(default="grid", pattern=r"^(grid|signal)$"),
) -> dict[str, Any]:
    """Одна ячейка. grid = сетка SL; signal = сделка PGv2 1:1. Окно графика —
    от касания; если касания нет — окрестность выставления (mode=placement)."""
    if mode == "signal":
        return _hb_build_single(signal_id, entry, lookforward, pre, refresh=refresh)
    data = _hb_build_matrix(signal_id, lookforward, pre, post, refresh=refresh)
    cell = next((c for c in data["cells"] if c["entry"] == entry and c["sl_index"] == sl), None)
    if cell is None:
        raise HTTPException(status_code=404, detail="cell not found")
    candles = data["candles"]
    if cell.get("touch_dt"):
        # якорь — касание: [touch-pre ... exit+post]
        lo, hi, mode = cell["lo"], cell["hi"], "touch"
    else:
        # касания нет: показываем окрестность выставления, а не 1000 баров пустоты
        sig_idx = data["sig_idx"]
        lo = max(0, sig_idx - pre)
        hi = min(len(candles), sig_idx + 96)
        cell = dict(cell)
        cell["lo"], cell["hi"] = lo, hi
        mode = "placement"
    result = dict(cell)
    result.pop("lo", None)
    result.pop("hi", None)
    result["window_end"] = data["window_end"]
    result["touches_outside"] = data["touches_outside"]
    result["mode"] = mode
    try:
        _sig2, _, _, _, _, _ = _hb_resolve_signal(signal_id)
        from level_tester.backtester.hourbounce import exec_from_signal as _efs
        pg_available = _efs(_sig2) is not None
    except Exception:
        pg_available = False
    return {
        "signal": data["signal"],
        "params": {"entry": entry, "sl_index": sl, "sl_pct": cell.get("sl_pct"), "mode": "grid"},
        "pg_available": pg_available,
        "result": result,
        "candles": candles[lo:hi],
        "cache": data["cache"],
    }


def _hb_price_str(v: object) -> str | None:
    """Цена без хвостов нулей: 0.029719800000000000 -> 0.0297198."""
    if v is None:
        return None
    try:
        d = v if isinstance(v, Decimal) else Decimal(str(v))
        return format(d.normalize(), "f")
    except Exception:
        return str(v)


def _hb_result_json(res) -> dict[str, Any]:
    return {
        "entry": res.entry_code, "sl_index": res.sl_index,
        "outcome": res.outcome, "reason": res.reason, "ambiguous": res.ambiguous,
        "touch_dt": res.touch_dt.isoformat() if res.touch_dt else None,
        "confirm_dt": [d.isoformat() for d in res.confirm_dt],
        "entry_dt": res.entry_dt.isoformat() if res.entry_dt else None,
        "entry_price": _hb_price_str(res.entry_price),
        "sl_price": _hb_price_str(res.sl_price),
        "exit_dt": res.exit_dt.isoformat() if res.exit_dt else None,
        "exit_price": _hb_price_str(res.exit_price),
        "exit_kind": res.exit_kind,
        "be_price": _hb_price_str(res.be_price),
        "r_multiple": str(round(res.r_multiple, 4)) if res.r_multiple is not None else None,
        "max_profit_pct": str(round(res.max_profit_pct, 4)) if res.max_profit_pct is not None else None,
        "mae_pct": str(round(res.mae_pct, 4)) if res.mae_pct is not None else None,
        "mfe_pct": str(round(res.mfe_pct, 4)) if res.mfe_pct is not None else None,
        "bars_in_trade": res.bars_in_trade,
        "events": [
            {"seq": e.seq, "type": e.type,
             "dt": e.dt.isoformat() if e.dt else None,
             "price": _hb_price_str(e.price)}
            for e in res.events
        ],
        "trail_path": [
            {"time": int(_hb_parse_time(p["dt"]).timestamp()) if _hb_parse_time(p["dt"]) else None,
             "value": float(p["trail"])} for p in res.trail_path
        ],
    }


@app.get("/api/hourbounce/matrix")
async def hourbounce_matrix(
    signal_id: str = Query(min_length=1),
    lookforward: int = Query(default=2000, ge=20, le=20000),
    pre: int = Query(default=30, ge=0, le=200),
    post: int = Query(default=30, ge=0, le=200),
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    """Все 9 комбинаций T1/T2/T3 x SL1/SL2/SL3 за одну загрузку свечей (с кешем)."""
    return _hb_build_matrix(signal_id, lookforward, pre, post, refresh=refresh)


@app.get("/api/hourbounce/export")
async def hourbounce_export(
    signal_id: str = Query(min_length=1),
    lookforward: int = Query(default=2000, ge=20, le=20000),
) -> FileResponse:
    """Self-contained HTML: 9 графиков + события + шапка сигнала."""
    import json as _json
    import tempfile as _tmp

    data = await hourbounce_matrix(signal_id=signal_id, lookforward=lookforward, pre=30, post=30)  # type: ignore[arg-type]
    payload = _json.dumps(data, ensure_ascii=False)
    sig = data["signal"]
    cards = ""
    for cell in data["cells"]:
        cards += (
            f'<div class="card"><div class="ch"><span>T{cell["entry"][1]} · SL{cell["sl_index"]} '
            f'({cell["sl_pct"]}%)</span><span class="badge">{cell["outcome"]}</span></div>'
            f'<div class="chart" id="ch-{cell["entry"]}-{cell["sl_index"]}"></div>'
            f'<div class="mt">entry {cell["entry_price"] or "—"} · exit {cell["exit_price"] or "—"}'
            f' · R {cell["r_multiple"] or "—"}</div></div>'
        )
    rows = ""
    for cell in data["cells"]:
        for e in cell["events"]:
            rows += f'<tr><td>T{cell["entry"][1]}/SL{cell["sl_index"]}</td><td>{e["seq"]}</td><td>{e["type"]}</td><td>{e["dt"] or "—"}</td><td>{e["price"] or "—"}</td></tr>'
    html = """<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<title>HourBounce __SYM__ __SID__</title>
<script src="https://unpkg.com/lightweight-charts@5/dist/lightweight-charts.standalone.production.js"></script>
<style>body{background:#0e1116;color:#d7dee8;font:13px sans-serif;margin:0;padding:16px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.card{background:#161b22;border:1px solid #2a323d;border-radius:8px;overflow:hidden}
.ch{display:flex;justify-content:space-between;padding:7px 10px;background:#1c232c;font-family:monospace}
.badge{background:#6b7280;color:#fff;border-radius:10px;padding:2px 8px;font-size:10px;font-weight:700}
.chart{height:220px}table{border-collapse:collapse;font-size:12px;margin-top:16px;width:100%}
td,th{border:1px solid #2a323d;padding:4px 8px;font-family:monospace;text-align:left}.mt{padding:6px 10px;color:#8b96a5;font-family:monospace;font-size:11px}</style>
</head><body>
<h2>__SYM__ · __SIDE__ · level __LVL__ · __DT__</h2>
<p>TP(arch) __TP__ · SL(arch) __SL__ · конфиг trail +1%/1%, окно подтверждения 20, окно жизни: вся история до ближайшей отработки</p>
<div class="grid">__CARDS__</div>
<table><tr><th>cell</th><th>seq</th><th>type</th><th>dt</th><th>price</th></tr>__ROWS__</table>
<script id="hb-data" type="application/json">__PAYLOAD__</script>
<script>
const D=JSON.parse(document.getElementById('hb-data').textContent),LC=LightweightCharts;
const C=D.candles;
const PREC=(D.signal&&D.signal.price_precision!=null)?D.signal.price_precision:4;
const TICK=Number(D.signal&&D.signal.tick_size);
const PF={type:'price',precision:PREC};
if(isFinite(TICK)&&TICK>0)PF.minMove=TICK;
for(const cell of D.cells){
 const el=document.getElementById('ch-'+cell.entry+'-'+cell.sl_index);
 const ch=LC.createChart(el,{layout:{background:{color:'transparent'},textColor:'#8b96a5'},grid:{vertLines:{color:'#1e2630'},horzLines:{color:'#1e2630'}},timeScale:{timeVisible:true,tickMarkFormatter:(t,type)=>{const d=new Date(t*1000),p=n=>String(n).padStart(2,'0'),hm=p(d.getUTCHours())+':'+p(d.getUTCMinutes())+' UTC';return type==='time'?hm:p(d.getUTCDate())+'.'+p(d.getUTCMonth()+1)+' '+hm;}}});
 const sl=C.slice(cell.lo,cell.hi);
 const lv=Number(D.signal.level_price);let mn=1/0,mx=-1/0;sl.forEach(c=>{mn=Math.min(mn,c.low);mx=Math.max(mx,c.high);});[cell.sl_price,cell.entry_price,cell.exit_price].concat((cell.trail_path||[]).map(p=>p.value)).forEach(v=>{if(v===null||v===undefined||v==='')return;v=Number(v);if(isFinite(v)){mn=Math.min(mn,v);mx=Math.max(mx,v);}});let RG=null;if(isFinite(lv)&&isFinite(mn)&&isFinite(mx)&&mn<mx){const half=Math.max(Math.abs(mx-lv),Math.abs(lv-mn),Math.abs(lv)*0.0005||0.000001)*1.15;RG={minValue:lv-half,maxValue:lv+half};}
 const s=ch.addSeries(LC.CandlestickSeries,{upColor:'#26a69a',downColor:'#ef5350',wickUpColor:'#26a69a',wickDownColor:'#ef5350',priceFormat:PF,autoscaleInfoProvider:(base)=>(RG?{priceRange:RG}:base())});
 s.setData(sl);
 s.createPriceLine({price:Number(D.signal.level_price),color:'#ff9f43',lineWidth:2,lineStyle:2,title:'H1'});
 if(cell.sl_price)s.createPriceLine({price:Number(cell.sl_price),color:'#ef5350',lineWidth:1,title:'SL'});
 const tp=(cell.trail_path||[]).filter(p=>p.time);
 if(tp.length){const t=ch.addSeries(LC.LineSeries,{color:'#26a69a',lineWidth:2,priceLineVisible:false,lastValueVisible:false});t.setData(tp);}
 const mk=[];const T={};
 C.forEach(c=>T[c.time]=1);
 const at=iso=>{if(!iso)return null;const t=Math.floor(new Date(iso).getTime()/1000);return T[t]?t:null;};
 if(cell.touch_dt)mk.push({time:at(cell.touch_dt),position:'belowBar',color:'#ff9f43',shape:'circle',text:'touch'});
 (cell.confirm_dt||[]).forEach((d,k)=>{if(at(d))mk.push({time:at(d),position:'aboveBar',color:'#2962ff',shape:'square',text:'C'+(k+1)});});
 if(cell.entry_dt&&at(cell.entry_dt))mk.push({time:at(cell.entry_dt),position:'belowBar',color:'#d7dee8',shape:'arrowUp',text:'entry'});
 if(cell.exit_dt&&at(cell.exit_dt))mk.push({time:at(cell.exit_dt),position:'aboveBar',color:cell.outcome==='TAKE'?'#26a69a':'#ef5350',shape:'square',text:cell.outcome});
 LC.createSeriesMarkers(s,mk.filter(m=>m.time));
 ch.timeScale().fitContent();
}
</script></body></html>"""
    html = (html.replace("__SYM__", str(sig["symbol"])).replace("__SID__", str(sig["signal_id"]))
            .replace("__SIDE__", str(sig["side"])).replace("__LVL__", str(sig["level_price"]))
            .replace("__DT__", str(sig["dt_place"])).replace("__TP__", str(sig.get("tp_arch")))
            .replace("__SL__", str(sig.get("sl_arch"))).replace("__CARDS__", cards)
            .replace("__ROWS__", rows).replace("__PAYLOAD__", payload))
    tmp = _tmp.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8")
    tmp.write(html)
    tmp.close()
    return FileResponse(tmp.name, media_type="text/html",
                        filename=f"hourbounce_{sig['symbol']}_{sig['signal_id']}.html")


# ---------------------------------------------------------------------------
# PnL-отчёт: все сигналы × 9 комбинаций (T1/T2/T3 × SL1/SL2/SL3).
# Клетка = PnL% со знаком стороны (нет входа -> 0), подвал = суммы по столбцам.
# ---------------------------------------------------------------------------
HB_REPORT_COLS = [f"{t}/SL{s}" for t in ("T1", "T2", "T3") for s in (1, 2, 3)]

_hb_report_jobs: dict[str, dict[str, Any]] = {}
_hb_report_counter = 0


def _hb_cell_pnl(cell: dict[str, Any], side: str) -> float:
    """PnL% клетки со знаком стороны. Нет входа/цены — 0."""
    try:
        if cell.get("outcome") == "NO_ENTRY" or not cell.get("entry_price") or not cell.get("exit_price"):
            return 0.0
        e = float(cell["entry_price"])
        x = float(cell["exit_price"])
        if e == 0:
            return 0.0
        r = (x - e) / e * 100 if side == "LONG" else (e - x) / e * 100
        return round(r, 4)
    except (TypeError, ValueError):
        return 0.0


class HbReportRequest(BaseModel):
    signal_ids: list[str] = Field(min_length=1, max_length=500)
    lookforward: int = Field(default=2000, ge=20, le=20000)


@app.post("/api/hourbounce/report", status_code=202)
async def hourbounce_report_run(request: HbReportRequest) -> dict[str, Any]:
    global _hb_report_counter
    _hb_report_counter += 1
    job_id = f"hbr_{_hb_report_counter}"
    _hb_report_jobs[job_id] = {
        "status": "pending", "done": 0, "total": len(request.signal_ids),
        "current": "", "result": None, "error": None,
    }
    Thread(target=_run_hb_report, args=(job_id, request.signal_ids, request.lookforward), daemon=True).start()
    return {"job_id": job_id, "status": "pending"}


@app.get("/api/hourbounce/report/status/{job_id}")
async def hourbounce_report_status(job_id: str) -> dict[str, Any]:
    job = _hb_report_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {k: v for k, v in job.items() if k != "result"}


@app.get("/api/hourbounce/report/result/{job_id}")
async def hourbounce_report_result(job_id: str) -> dict[str, Any]:
    job = _hb_report_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] != "completed":
        raise HTTPException(status_code=409, detail="job not completed yet")
    return job["result"]


def _hb_archive_trades(signal_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Статус/PnL архива пачкой: traded=True только у реально отторгованных (closed_*)."""
    out: dict[str, dict[str, Any]] = {}
    try:
        import sqlite3 as _sqlite3

        adb = Path(str(PGV2_ARCHIVE_DB))
        if not adb.exists() or not signal_ids:
            return out
        conn = _sqlite3.connect(f"file:{adb.as_posix()}?mode=ro", uri=True)
        try:
            conn.row_factory = _sqlite3.Row
            q = f"SELECT original_id, id, status, close_reason, pnl, pnl_percent FROM signal_archive WHERE original_id IN ({','.join('?' * len(signal_ids))}) OR id IN ({','.join('?' * len(signal_ids))})"
            for r in conn.execute(q, (*signal_ids, *signal_ids)).fetchall():
                keys = set(r.keys())
                status = r["status"] if "status" in keys else None
                traded = isinstance(status, str) and status.startswith("closed_")
                info = {
                    "status": status,
                    "close_reason": r["close_reason"] if "close_reason" in keys else None,
                    "pnl": r["pnl"] if "pnl" in keys else None,
                    "pnl_percent": r["pnl_percent"] if "pnl_percent" in keys else None,
                    "traded": traded,
                }
                for k in (r["original_id"], r["id"]):
                    if k:
                        out[str(k)] = info
        finally:
            conn.close()
    except Exception:
        pass
    return out


def _run_hb_report(job_id: str, signal_ids: list[str], lookforward: int) -> None:
    job = _hb_report_jobs[job_id]
    rows: list[dict[str, Any]] = []
    footer = {c: 0.0 for c in HB_REPORT_COLS}
    try:
        job["status"] = "running"
        arch = _hb_archive_trades(signal_ids)
        for i, sid in enumerate(signal_ids):
            job["current"] = sid
            info = arch.get(sid, {})
            try:
                data = _hb_build_matrix(sid, lookforward, 30, 30)
                sig = data["signal"]
                by_key = {(c["entry"], c["sl_index"]): c for c in data["cells"]}
                cells = {}
                row_total = 0.0
                for col in HB_REPORT_COLS:
                    t, s = col.split("/")
                    cell = by_key.get((t, int(s[2:])))
                    pnl = _hb_cell_pnl(cell or {}, sig["side"]) if cell else 0.0
                    outcome = (cell or {}).get("outcome")
                    cells[col] = {"pnl": pnl, "outcome": outcome}
                    footer[col] = round(footer[col] + pnl, 4)
                    row_total = round(row_total + pnl, 4)
                rows.append({
                    "signal_id": sid, "symbol": sig["symbol"], "side": sig["side"],
                    "level_price": sig["level_price"], "dt_place": sig["dt_place"],
                    "outcome_arch": None, "cells": cells, "row_total": row_total,
                    "arch_status": info.get("status"), "arch_pnl": info.get("pnl_percent"),
                    "traded": bool(info.get("traded")),
                })
            except Exception as exc:  # noqa: BLE001 - один битый сигнал не роняет отчёт
                logger.debug("hourbounce report signal failed: %s", sid, exc_info=True)
                rows.append({"signal_id": sid, "symbol": "?", "side": "?",
                             "level_price": None, "dt_place": None, "outcome_arch": None,
                             "cells": {c: {"pnl": 0.0, "outcome": "ERROR"} for c in HB_REPORT_COLS},
                             "row_total": 0.0, "error": str(exc)[:200],
                             "arch_status": info.get("status"), "arch_pnl": info.get("pnl_percent"),
                             "traded": bool(info.get("traded"))})
            job["done"] = i + 1
        footer["TOTAL"] = round(sum(footer.values()), 4)
        job["result"] = {"columns": HB_REPORT_COLS, "rows": rows, "footer": footer,
                         "count": len(rows)}
        job["status"] = "completed"
    except Exception as exc:  # noqa: BLE001 - граница фонового job
        job["status"] = "failed"
        job["error"] = str(exc)[:500]
