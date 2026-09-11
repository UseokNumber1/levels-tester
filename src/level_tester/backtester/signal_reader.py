"""Read signals from precision_grid_v2 databases (trading.db / signal_archive.db)."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


def _to_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


def _to_bool(v: Any) -> Optional[bool]:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    return None


@dataclass(frozen=True, slots=True)
class SignalEntry:
    signal_id: str
    symbol: str
    side: str  # LONG | SHORT
    entry_price: float
    stop_loss: Optional[float]
    rr_ratio: Optional[float]
    timeframe: str
    timestamp: Optional[str]  # ISO or None
    source: str  # 'trading' | 'archive'
    # --- P_G_V2 execution snapshot (None = not stored, use live fallback) ---
    stop_loss_pct: Optional[float] = None
    take_profits: Optional[str] = None  # JSON array as stored in DB
    take_profit_pct: Optional[float] = None
    stop_loss_source: Optional[str] = None
    take_profit_source: Optional[str] = None
    breakeven_enabled: Optional[bool] = None
    breakeven_trigger_pct: Optional[float] = None
    breakeven_profit_pct: Optional[float] = None
    breakeven_fix_enabled: Optional[bool] = None
    breakeven_fix_pct: Optional[float] = None
    trailing_stop_enabled: Optional[bool] = None
    trailing_activation_pct: Optional[float] = None
    trailing_stop_pct: Optional[float] = None
    trailing_update_threshold_pct: Optional[float] = None
    trailing_tp_only: Optional[bool] = None
    # --- состояние сделки в архиве (для отличия pristine-SL от подвинутого) ---
    confirmation_timeframe: Optional[str] = None  # 1m | 5m — TF подтверждения PGv2
    trailing_activated: Optional[bool] = None  # трейлинг успел включиться
    sl_moved_to_breakeven: Optional[bool] = None  # стоп уже двигали в БУ


class SignalReader:
    """Read signals from PGv2 SQLite databases."""

    def __init__(
        self,
        trading_db: str | Path,
        archive_db: str | Path | None = None,
    ) -> None:
        self._trading_db = Path(trading_db)
        self._archive_db = Path(archive_db) if archive_db else None

    # Statuses that indicate a validated, "working" level in PGv2.
    # low_quality / blacklisted / cancelled — signals rejected before/after confirmation, without a working level.
    WORKING_STATUSES = ("entry_waiting_confirmation", "entry_confirmed", "closed_sl", "closed_tp")

    def read(
        self,
        symbol: str | None = None,
        side: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        signal_id: str | None = None,
        include_trading: bool = False,
        include_archive: bool = True,
        limit: int = 500,
        only_working_level: bool = False,
    ) -> list[SignalEntry]:
        """Read signals with optional filters.

        Args:
            symbol: e.g. 'BTCUSDT' or None for all
            side: 'LONG' or 'SHORT' or None for both
            period_start: ISO date e.g. '2026-08-01'
            period_end: ISO date e.g. '2026-08-31'
            signal_id: exact signal ID (overrides other filters)
            include_trading: also read trading.db
            include_archive: also read signal_archive.db
            limit: max signals to return
        """
        results: list[SignalEntry] = []

        if signal_id:
            if include_trading:
                results.extend(self._read_trading(signal_id=signal_id, only_working_level=only_working_level))
            if include_archive and self._archive_db:
                results.extend(self._read_archive(signal_id=signal_id))
            # when fetching by exact id, apply working-level filter post-hoc
            if only_working_level:
                results = [r for r in results if self._is_working(r)]
            return results

        if include_trading:
            results.extend(self._read_trading(
                symbol=symbol, side=side,
                period_start=period_start, period_end=period_end,
                limit=limit,
                only_working_level=only_working_level,
            ))

        if include_archive and self._archive_db:
            remaining = limit - len(results)
            if remaining > 0:
                results.extend(self._read_archive(
                    symbol=symbol, side=side,
                    period_start=period_start, period_end=period_end,
                    limit=remaining,
                ))

        return results

    def symbols(self, include_archive: bool = True) -> list[str]:
        """Return distinct symbols from both databases."""
        syms: set[str] = set()
        if self._trading_db.exists():
            syms.update(self._query_symbols(self._trading_db, "signals"))
        if include_archive and self._archive_db and self._archive_db.exists():
            syms.update(self._query_symbols(self._archive_db, "signal_archive"))
        return sorted(syms)

    @staticmethod
    def _is_working(entry: "SignalEntry") -> bool:
        # Archive entries have no status column — treat them as working (already closed).
        # Trading entries would need status check, but we push it to SQL when possible.
        return True

    SNAPSHOT_COLS = (
        "stop_loss_pct", "take_profits", "take_profit_pct",
        "stop_loss_source", "take_profit_source",
        "breakeven_enabled", "breakeven_trigger_pct", "breakeven_profit_pct",
        "breakeven_fix_enabled", "breakeven_fix_pct",
        "trailing_stop_enabled", "trailing_activation_pct", "trailing_stop_pct",
        "trailing_update_threshold_pct", "trailing_tp_only",
    )

    def _existing_cols(self, conn: sqlite3.Connection, table: str) -> set[str]:
        try:
            return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except Exception:
            return set()

    def _read_trading(
        self,
        symbol: str | None = None,
        side: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        signal_id: str | None = None,
        limit: int = 500,
        only_working_level: bool = False,
    ) -> list[SignalEntry]:
        if not self._trading_db.exists():
            return []
        conn = sqlite3.connect(f"file:{self._trading_db.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            existing = self._existing_cols(conn, "signals")
            snap = [c for c in self.SNAPSHOT_COLS if c in existing]
            cols = ("id, symbol, side, entry_price, stop_loss, rr_ratio, "
                    "timeframe, timestamp, created_at")
            if snap:
                cols += ", " + ", ".join(snap)
            query = f"SELECT {cols} FROM signals WHERE 1=1"
            params: list = []

            if signal_id:
                query += " AND id = ?"
                params.append(signal_id)
            if symbol:
                query += " AND symbol = ?"
                params.append(symbol.upper())
            if side:
                query += " AND side = ?"
                params.append(side.upper())
            if period_start:
                query += " AND (timestamp >= ? OR created_at >= ?)"
                params.extend([period_start, period_start])
            if period_end:
                query += " AND (timestamp <= ? OR created_at <= ?)"
                params.extend([period_end + "T23:59:59", period_end + "T23:59:59"])

            if only_working_level:
                # Only signals with a validated working level: exclude rejected / low-quality.
                # In trading.db level_side is NULL for all rows, so we filter by status + price sanity.
                placeholders = ",".join("?" for _ in self.WORKING_STATUSES)
                query += f" AND status IN ({placeholders})"
                params.extend(self.WORKING_STATUSES)
                query += " AND entry_price IS NOT NULL AND stop_loss IS NOT NULL"

            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [self._row_to_signal(row, "trading") for row in rows]
        finally:
            conn.close()

    def _read_archive(
        self,
        symbol: str | None = None,
        side: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        signal_id: str | None = None,
        limit: int = 500,
    ) -> list[SignalEntry]:
        if not self._archive_db or not self._archive_db.exists():
            return []
        conn = sqlite3.connect(f"file:{self._archive_db.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            query = (
                "SELECT original_id, symbol, side, entry_price, stop_loss, "
                "rr_ratio, created_at, metadata, take_profits FROM signal_archive WHERE 1=1"
            )
            params: list = []

            if signal_id:
                query += " AND (original_id = ? OR id = ?)"
                params.extend([signal_id, signal_id])
            if symbol:
                query += " AND symbol = ?"
                params.append(symbol.upper())
            if side:
                query += " AND side = ?"
                params.append(side.upper())
            if period_start:
                query += " AND created_at >= ?"
                params.append(period_start)
            if period_end:
                query += " AND created_at <= ?"
                params.append(period_end + "T23:59:59")

            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            results = []
            for row in rows:
                sid = row["original_id"] or row["symbol"]
                snap = self._parse_archive_metadata(row["metadata"] if "metadata" in row.keys() else None)
                tp_raw = row["take_profits"] if "take_profits" in row.keys() else None
                results.append(SignalEntry(
                    signal_id=sid,
                    symbol=row["symbol"],
                    side=row["side"],
                    entry_price=float(row["entry_price"]) if row["entry_price"] else 0.0,
                    stop_loss=float(row["stop_loss"]) if row["stop_loss"] else None,
                    rr_ratio=float(row["rr_ratio"]) if row["rr_ratio"] else None,
                    timeframe="1h",
                    timestamp=row["created_at"],
                    source="archive",
                    take_profits=str(tp_raw) if tp_raw else snap.get("take_profits"),
                    stop_loss_pct=_to_float(snap.get("stop_loss_pct")),
                    take_profit_pct=_to_float(snap.get("take_profit_pct")),
                    stop_loss_source=snap.get("stop_loss_source"),
                    take_profit_source=snap.get("take_profit_source"),
                    breakeven_enabled=_to_bool(snap.get("breakeven_enabled")),
                    breakeven_trigger_pct=_to_float(snap.get("breakeven_trigger_pct")),
                    breakeven_profit_pct=_to_float(snap.get("breakeven_profit_pct")),
                    breakeven_fix_enabled=_to_bool(snap.get("breakeven_fix_enabled")),
                    breakeven_fix_pct=_to_float(snap.get("breakeven_fix_pct")),
                    trailing_stop_enabled=_to_bool(snap.get("trailing_stop_enabled")),
                    trailing_activation_pct=_to_float(snap.get("trailing_activation_pct")),
                    trailing_stop_pct=_to_float(snap.get("trailing_stop_pct")),
                    trailing_update_threshold_pct=_to_float(snap.get("trailing_update_threshold_pct")),
                    trailing_tp_only=_to_bool(snap.get("trailing_tp_only")),
                    confirmation_timeframe=snap.get("confirmation_timeframe"),
                    trailing_activated=_to_bool(snap.get("trailing_activated")),
                    sl_moved_to_breakeven=_to_bool(snap.get("sl_moved_to_breakeven")),
                ))
            return results
        finally:
            conn.close()

    @staticmethod
    def _parse_archive_metadata(raw: object) -> dict:
        if not raw:
            return {}
        try:
            import json as _json
            data = _json.loads(raw) if isinstance(raw, str) else {}
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _row_to_signal(row: sqlite3.Row, source: str) -> SignalEntry:
        keys = set(row.keys())
        def _g(col: str):
            return row[col] if col in keys else None
        return SignalEntry(
            signal_id=row["id"],
            symbol=row["symbol"],
            side=row["side"],
            entry_price=float(row["entry_price"]) if row["entry_price"] else 0.0,
            stop_loss=float(row["stop_loss"]) if row["stop_loss"] else None,
            rr_ratio=float(row["rr_ratio"]) if row["rr_ratio"] else None,
            timeframe=row["timeframe"] or "1h",
            timestamp=row["timestamp"] or row["created_at"],
            source=source,
            stop_loss_pct=_to_float(_g("stop_loss_pct")),
            take_profits=str(_g("take_profits")) if _g("take_profits") else None,
            take_profit_pct=_to_float(_g("take_profit_pct")),
            stop_loss_source=_g("stop_loss_source"),
            take_profit_source=_g("take_profit_source"),
            breakeven_enabled=_to_bool(_g("breakeven_enabled")),
            breakeven_trigger_pct=_to_float(_g("breakeven_trigger_pct") if _g("breakeven_trigger_pct") is not None else _g("breakeven_trigger")),
            breakeven_profit_pct=_to_float(_g("breakeven_profit_pct") if _g("breakeven_profit_pct") is not None else _g("breakeven_profit")),
            breakeven_fix_enabled=_to_bool(_g("breakeven_fix_enabled")),
            breakeven_fix_pct=_to_float(_g("breakeven_fix_pct")),
            trailing_stop_enabled=_to_bool(_g("trailing_stop_enabled")),
            trailing_activation_pct=_to_float(_g("trailing_activation_pct") if _g("trailing_activation_pct") is not None else _g("trailing_activation")),
            trailing_stop_pct=_to_float(_g("trailing_stop_pct") if _g("trailing_stop_pct") is not None else _g("trailing_distance")),
            trailing_update_threshold_pct=_to_float(_g("trailing_update_threshold_pct") if _g("trailing_update_threshold_pct") is not None else _g("trailing_update_threshold")),
            trailing_tp_only=_to_bool(_g("trailing_tp_only")),
            confirmation_timeframe=_g("confirmation_timeframe"),
            trailing_activated=None,
            sl_moved_to_breakeven=None,
        )

    @staticmethod
    def _query_symbols(db_path: Path, table: str) -> set[str]:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                f"SELECT DISTINCT symbol FROM {table} WHERE symbol IS NOT NULL"
            ).fetchall()
            return {row[0] for row in rows if row[0]}
        finally:
            conn.close()
