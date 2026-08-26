from datetime import UTC, datetime, timedelta
from decimal import Decimal

from level_tester.domain.models import Candle
from level_tester.infrastructure.quality import inspect_candles


def test_quality_report_exposes_gaps_instead_of_interpolating() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    bars = [
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
    report = inspect_candles(bars, timedelta(hours=1))
    assert report.gap_count == 1
    assert not report.is_valid
