from __future__ import annotations

import logging
import os
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
    return {
        "items": [
            {
                "id": v.id,
                "name": v.name,
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
        ],
        "count": len(BUILTIN_VARIANTS),
    }


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
            if vid in custom_by_id:
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
            if r.trade is not None:
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
                }
                for r in all_results
                if r.trade is not None
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
