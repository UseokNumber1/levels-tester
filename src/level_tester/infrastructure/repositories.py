from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from level_tester.domain.models import Candle
from level_tester.infrastructure.database import CandleRow, ReplayCheckpointRow, RunRow


class CandleRepository:
    """Idempotent candle upsert keyed by instrument, timeframe and open_time."""

    def upsert_many(self, session: Session, instrument_id: int, candles: list[Candle]) -> int:
        changed = 0
        for candle in candles:
            row = session.scalar(
                select(CandleRow).where(
                    CandleRow.instrument_id == instrument_id,
                    CandleRow.timeframe == candle.timeframe,
                    CandleRow.open_time == candle.open_time,
                )
            )
            if row is None:
                row = CandleRow(
                    instrument_id=instrument_id,
                    timeframe=candle.timeframe,
                    open_time=candle.open_time,
                )
                session.add(row)
            row.close_time = candle.close_time
            row.open = candle.open
            row.high = candle.high
            row.low = candle.low
            row.close = candle.close
            row.volume = candle.volume
            changed += 1
        session.commit()
        return changed

    def list_range(
        self, session: Session, instrument_id: int, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        rows = session.scalars(
            select(CandleRow)
            .where(
                CandleRow.instrument_id == instrument_id,
                CandleRow.timeframe == timeframe,
                CandleRow.open_time >= start,
                CandleRow.open_time < end,
            )
            .order_by(CandleRow.open_time)
        )
        return [
            Candle(
                row.open_time.replace(tzinfo=UTC)
                if row.open_time.tzinfo is None
                else row.open_time,
                row.close_time.replace(tzinfo=UTC)
                if row.close_time.tzinfo is None
                else row.close_time,
                row.open,
                row.high,
                row.low,
                row.close,
                row.volume,
                row.timeframe,
            )
            for row in rows
        ]

    def count_range(
        self, session: Session, instrument_id: int, timeframe: str, start: datetime, end: datetime
    ) -> int:
        return (
            session.query(CandleRow)
            .filter(
                CandleRow.instrument_id == instrument_id,
                CandleRow.timeframe == timeframe,
                CandleRow.open_time >= start,
                CandleRow.open_time < end,
            )
            .count()
        )

    def open_times(
        self, session: Session, instrument_id: int, timeframe: str, start: datetime, end: datetime
    ) -> set[datetime]:
        values = session.scalars(
            select(CandleRow.open_time).where(
                CandleRow.instrument_id == instrument_id,
                CandleRow.timeframe == timeframe,
                CandleRow.open_time >= start,
                CandleRow.open_time < end,
            )
        )
        return {
            value.replace(tzinfo=UTC) if value.tzinfo is None else value
            for value in values
        }


class RunRepository:
    def save_run(
        self,
        session: Session,
        run_id: str,
        symbol: str,
        display_from: datetime,
        calculation_from: datetime,
        effective_to: datetime,
        status: str,
        config_hash: str,
        settings: dict,
        data_set_id: str = "",
        progress: int = 0,
        error_message: str | None = None,
        cursor_time: datetime | None = None,
    ) -> None:
        row = session.get(RunRow, run_id) or RunRow(id=run_id)
        row.symbol = symbol
        row.display_from = display_from
        row.calculation_from = calculation_from
        row.effective_to = effective_to
        row.status = status
        row.config_hash = config_hash
        row.data_set_id = data_set_id
        row.progress = progress
        row.error_message = error_message
        row.cursor_time = cursor_time
        row.settings_json = json.dumps(settings, sort_keys=True)
        session.add(row)
        session.commit()

    def save_checkpoint(
        self, session: Session, run_id: str, sequence: int, cursor_time: datetime, state: dict
    ) -> None:
        session.add(
            ReplayCheckpointRow(
                run_id=run_id,
                sequence=sequence,
                cursor_time=cursor_time,
                state_json=json.dumps(state, sort_keys=True),
            )
        )
        session.commit()
