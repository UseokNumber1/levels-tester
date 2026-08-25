from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from level_tester.infrastructure.binance import BinanceFuturesClient
from level_tester.infrastructure.instruments import InstrumentRepository


class InstrumentService:
    def __init__(
        self, client: BinanceFuturesClient, repository: InstrumentRepository | None = None
    ) -> None:
        self.client = client
        self.repository = repository or InstrumentRepository()

    def sync(self, session: Session) -> int:
        ticker_by_symbol = {
            item.get("symbol"): item for item in self.client.ticker_24h() if item.get("symbol")
        }
        instruments = []
        for item in self.client.exchange_info():
            if item.get("quoteAsset") != "USDT" or item.get("contractType") not in {
                None,
                "PERPETUAL",
            }:
                continue
            ticker = ticker_by_symbol.get(item.get("symbol"), {})
            instruments.append(
                {
                    "symbol": item.get("symbol", ""),
                    "base_asset": item.get("baseAsset", ""),
                    "quote_asset": item.get("quoteAsset", "USDT"),
                    "status": item.get("status", "UNKNOWN"),
                    "daily_volume": Decimal(str(ticker.get("quoteVolume", "0"))),
                }
            )
        return self.repository.upsert_many(session, instruments)

    def search(
        self,
        session: Session,
        search: str = "",
        min_volume: Decimal | None = None,
        max_volume: Decimal | None = None,
        limit: int = 100,
    ) -> list:
        return self.repository.search(
            session, search, min_volume=min_volume, max_volume=max_volume, limit=limit
        )
