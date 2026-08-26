from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Thread
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from level_tester.application.ingestion import DataIngestionService
from level_tester.application.instruments import InstrumentService
from level_tester.application.run_service import RunService
from level_tester.domain.models import Candle
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
    # Optional closed candles are useful for local imports and deterministic tests.
    seed_candles: list[CandleInput] = Field(default_factory=list)

    @field_validator("display_from", "calculation_from", "effective_to")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must include a timezone")
        return value


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


app = FastAPI(title="Levels Tester", version="0.4.3", lifespan=lifespan)
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
        }
    }


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


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
            request.symbol, display_from, calculation_from, effective_to, request.detail_timeframe
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
        config = replace(
            default_config,
            detail_timeframe=run.detail_timeframe,
            confirmation=replace(
                default_config.confirmation, timeframe=run.detail_timeframe
            ),
        )
        service.complete_loading(run_id, candles, None, config, instrument.id)
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
                {"detail_timeframe": run.detail_timeframe},
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
