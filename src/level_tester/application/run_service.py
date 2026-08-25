from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

from level_tester.domain.models import Candle
from level_tester.domain.replay import ReplayConfig, ReplayEngine, ReplayStatus, ReplayWindow


@dataclass(slots=True)
class Run:
    id: str
    symbol: str
    display_from: datetime
    effective_to: datetime
    window: ReplayWindow
    engine: ReplayEngine | None = None
    status: ReplayStatus = ReplayStatus.LOADING
    speed: float = 1.0
    config_hash: str = ""
    data_set_id: str = ""
    progress: int = 0
    loading_stage: str = ""
    error_message: str | None = None
    instrument_id: int | None = None
    detail_timeframe: str = "1m"


class RunService:
    def __init__(self, default_config: ReplayConfig | None = None) -> None:
        self._runs: dict[str, Run] = {}
        self._lock = RLock()
        self.default_config = default_config

    def create_loading(
        self,
        symbol: str,
        display_from: datetime,
        calculation_from: datetime,
        effective_to: datetime,
        detail_timeframe: str = "1m",
    ) -> Run:
        _validate_symbol(symbol)
        window = ReplayWindow(display_from, effective_to, calculation_from)
        run = Run(
            id=str(uuid4()),
            symbol=symbol.upper(),
            display_from=window.display_from,
            effective_to=window.effective_to,
            window=window,
            detail_timeframe=detail_timeframe,
        )
        with self._lock:
            self._runs[run.id] = run
        return run

    def complete_loading(
        self,
        run_id: str,
        master_candles: list[Candle],
        detail_candles: list[Candle] | None,
        replay_config: ReplayConfig,
        instrument_id: int | None = None,
    ) -> Run:
        run = self.get(run_id)
        run.engine = ReplayEngine(run.id, run.window, master_candles, detail_candles, replay_config)
        run.config_hash = hashlib.sha256(repr(replay_config).encode()).hexdigest()
        run.data_set_id = hashlib.sha256(
            json.dumps([_candle_key(candle) for candle in master_candles], sort_keys=True).encode()
        ).hexdigest()
        run.progress = 100
        run.loading_stage = ""
        run.error_message = None
        run.instrument_id = instrument_id
        run.status = ReplayStatus.READY
        return run

    def fail_loading(self, run_id: str, message: str) -> Run:
        run = self.get(run_id)
        run.error_message = message[:2000]
        run.status = ReplayStatus.FAILED
        return run

    def update_loading(self, run_id: str, progress: int, stage: str = "") -> Run:
        run = self.get(run_id)
        run.progress = max(0, min(99, progress))
        if stage:
            run.loading_stage = stage
        return run

    def attach_detail(self, run_id: str, candles: list[Candle]) -> dict:
        run = self.get(run_id)
        if run.engine is None:
            raise ValueError("run is still loading")
        run.engine.set_detail_candles(candles)
        run.data_set_id = hashlib.sha256(
            json.dumps(
                {
                    "master_data_set": run.data_set_id,
                    "detail": [_candle_key(candle) for candle in run.engine.detail_candles],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return self.snapshot(run_id)

    def get(self, run_id: str) -> Run:
        with self._lock:
            try:
                return self._runs[run_id]
            except KeyError as exc:
                raise KeyError(f"run {run_id} was not found") from exc

    def command(self, run_id: str, command: str) -> dict:
        run = self.get(run_id)
        if run.engine is None:
            raise ValueError("run is still loading")
        with self._lock:
            if command in {"play", "resume"}:
                run.engine.play()
            elif command == "step":
                run.engine.step()
            elif command == "pause":
                run.engine.pause()
            elif command == "reset":
                run.engine.reset()
            elif command == "cancel":
                run.engine.cancel()
            else:
                raise ValueError(f"unsupported replay command: {command}")
            run.status = run.engine.status
            return self.snapshot(run_id)

    def set_speed(self, run_id: str, speed: float) -> dict:
        if speed <= 0 or speed > 100:
            raise ValueError("speed must be between 0 and 100")
        run = self.get(run_id)
        if run.engine is None:
            raise ValueError("run is still loading")
        run.speed = speed
        return self.snapshot(run_id)

    def snapshot(self, run_id: str) -> dict:
        run = self.get(run_id)
        if run.engine is None:
            return _loading_snapshot(run)
        snapshot = run.engine.snapshot()
        snapshot.update(
            {
                "symbol": run.symbol,
                "speed": run.speed,
                "config_hash": run.config_hash,
                "data_set_id": run.data_set_id,
                "progress": run.progress,
                "error_message": run.error_message,
                "window": _window_json(run.window),
            }
        )
        return snapshot


def _validate_symbol(symbol: str) -> None:
    if not symbol or not symbol.isascii() or not symbol.isalnum() or len(symbol) > 30:
        raise ValueError("symbol must be an ASCII market symbol")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetimes must be timezone-aware UTC values")
    return value.astimezone(timezone.utc)


def _window_json(window: ReplayWindow) -> dict[str, str]:
    return {
        "display_from": window.display_from.isoformat(),
        "calculation_from": window.calculation_from.isoformat(),
        "effective_to": window.effective_to.isoformat(),
    }


def _loading_snapshot(run: Run) -> dict:
    return {
        "run_id": run.id,
        "symbol": run.symbol,
        "status": run.status,
        "progress": run.progress,
        "loading_stage": run.loading_stage,
        "error_message": run.error_message,
        "cursor": None,
        "pivots": [],
        "levels": [],
        "outcomes": [],
        "master_candles": [],
        "detail_candles": [],
        "events": [],
        "speed": run.speed,
        "config_hash": run.config_hash,
        "data_set_id": run.data_set_id,
        "window": _window_json(run.window),
    }


def _candle_key(candle: Candle) -> dict[str, str]:
    return {
        "timeframe": candle.timeframe,
        "open_time": candle.open_time.isoformat(),
        "close_time": candle.close_time.isoformat(),
        "open": str(candle.open),
        "high": str(candle.high),
        "low": str(candle.low),
        "close": str(candle.close),
        "volume": str(candle.volume),
    }
