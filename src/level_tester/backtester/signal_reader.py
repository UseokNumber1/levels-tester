"""Read signals from precision_grid_v2 databases (trading.db / signal_archive.db)."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


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
            query = (
                "SELECT id, symbol, side, entry_price, stop_loss, rr_ratio, "
                "timeframe, timestamp, created_at FROM signals WHERE 1=1"
            )
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
                "rr_ratio, created_at FROM signal_archive WHERE 1=1"
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
                ))
            return results
        finally:
            conn.close()

    @staticmethod
    def _row_to_signal(row: sqlite3.Row, source: str) -> SignalEntry:
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
