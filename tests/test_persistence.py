from datetime import datetime, timedelta, timezone
from decimal import Decimal

from level_tester.domain.models import Candle
from level_tester.infrastructure.database import (
    CandleRow,
    InstrumentRow,
    create_session_factory,
    ensure_schema,
)
from level_tester.infrastructure.repositories import CandleRepository


def test_candle_upsert_is_idempotent() -> None:
    session_factory = create_session_factory("sqlite://")
    ensure_schema(session_factory)
    session = session_factory()
    instrument = InstrumentRow(
        exchange="binance", market_type="usdt_m_futures", symbol="BTCUSDT", quote_asset="USDT"
    )
    session.add(instrument)
    session.commit()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candle = Candle(
        start,
        start + timedelta(hours=1),
        Decimal("1"),
        Decimal("2"),
        Decimal("0"),
        Decimal("1"),
        Decimal("3"),
    )
    repository = CandleRepository()
    assert repository.upsert_many(session, instrument.id, [candle]) == 1
    assert repository.upsert_many(session, instrument.id, [candle]) == 1
    session.close()
    check = session_factory()
    assert check.query(type(instrument)).count() == 1
    assert check.query(CandleRow).count() == 1
