from datetime import UTC, datetime, timedelta
from decimal import Decimal

from level_tester.application.ingestion import DataIngestionService
from level_tester.application.instruments import InstrumentService
from level_tester.domain.models import Candle
from level_tester.infrastructure.database import create_session_factory, ensure_schema
from level_tester.infrastructure.repositories import CandleRepository


class FakeBinance:
    def exchange_info(self):
        return [
            {
                "symbol": "BTCUSDT",
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                "status": "TRADING",
                "contractType": "PERPETUAL",
            },
            {
                "symbol": "BTCUSD",
                "baseAsset": "BTC",
                "quoteAsset": "USD",
                "status": "TRADING",
                "contractType": "PERPETUAL",
            },
        ]

    def ticker_24h(self):
        return [{"symbol": "BTCUSDT", "quoteVolume": "25000000"}]


def test_instrument_sync_stores_usdt_quote_volume_and_filters() -> None:
    factory = create_session_factory("sqlite://")
    ensure_schema(factory)
    service = InstrumentService(FakeBinance())
    with factory() as session:
        assert service.sync(session) == 1
        rows = service.search(session, search="btc", min_volume=Decimal(20000000))
        assert [row.symbol for row in rows] == ["BTCUSDT"]
        assert rows[0].daily_volume == Decimal(25000000)


def test_coverage_returns_missing_open_time_range() -> None:
    factory = create_session_factory("sqlite://")
    ensure_schema(factory)
    repository = CandleRepository()
    ingestion = DataIngestionService(FakeBinance())
    start = datetime(2026, 1, 1, tzinfo=UTC)
    with factory() as session:
        from level_tester.infrastructure.database import InstrumentRow

        instrument = InstrumentRow(
            symbol="BTCUSDT", exchange="binance", market_type="usdt_m_futures", quote_asset="USDT"
        )
        session.add(instrument)
        session.commit()
        candles = [
            Candle(
                start,
                start + timedelta(hours=1),
                Decimal(1),
                Decimal(2),
                Decimal(0),
                Decimal(1),
                Decimal(1),
            ),
            Candle(
                start + timedelta(hours=2),
                start + timedelta(hours=3),
                Decimal(1),
                Decimal(2),
                Decimal(0),
                Decimal(1),
                Decimal(1),
            ),
        ]
        repository.upsert_many(session, instrument.id, candles)
        coverage = ingestion.coverage(
            session, instrument.id, "1h", start, start + timedelta(hours=3)
        )
        assert coverage.stored_count == 2
        assert coverage.missing_count == 1
        assert coverage.missing_ranges == [(start + timedelta(hours=1), start + timedelta(hours=2))]
