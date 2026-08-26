from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from level_tester.infrastructure.database import InstrumentRow


class InstrumentRepository:
    def upsert_many(self, session: Session, instruments: list[dict]) -> int:
        now = datetime.now(UTC)
        changed = 0
        for item in instruments:
            row = session.scalar(
                select(InstrumentRow).where(InstrumentRow.symbol == item["symbol"])
            )
            if row is None:
                row = InstrumentRow(symbol=item["symbol"])
                session.add(row)
            row.exchange = item.get("exchange", "binance")
            row.market_type = item.get("market_type", "usdt_m_futures")
            row.quote_asset = item.get("quote_asset", "USDT")
            row.base_asset = item.get("base_asset", "")
            row.status = item.get("status", "TRADING")
            row.daily_volume = Decimal(str(item.get("daily_volume", "0")))
            row.last_synced_at = now
            changed += 1
        session.commit()
        return changed

    def find(self, session: Session, symbol: str) -> InstrumentRow | None:
        return session.scalar(select(InstrumentRow).where(InstrumentRow.symbol == symbol.upper()))

    def search(
        self,
        session: Session,
        search: str = "",
        quote_asset: str = "USDT",
        status: str = "TRADING",
        min_volume: Decimal | None = None,
        max_volume: Decimal | None = None,
        limit: int = 100,
    ) -> list[InstrumentRow]:
        query = select(InstrumentRow).where(
            InstrumentRow.quote_asset == quote_asset.upper(), InstrumentRow.status == status.upper()
        )
        if search:
            query = query.where(InstrumentRow.symbol.like(f"%{search.upper()}%"))
        if min_volume is not None:
            query = query.where(InstrumentRow.daily_volume >= min_volume)
        if max_volume is not None:
            query = query.where(InstrumentRow.daily_volume <= max_volume)
        return list(session.scalars(query.order_by(InstrumentRow.daily_volume.desc()).limit(limit)))
