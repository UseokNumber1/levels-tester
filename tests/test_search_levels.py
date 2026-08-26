from datetime import UTC, datetime, timedelta
from decimal import Decimal

from level_tester.domain.models import Candle, LevelState, Pivot, PivotKind
from level_tester.domain.search import LevelBook, LevelConfig


def _candle(index: int, high: str, low: str) -> Candle:
    opened = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=index)
    return Candle(
        opened,
        opened + timedelta(hours=1),
        Decimal(100),
        Decimal(high),
        Decimal(low),
        Decimal(100),
        Decimal(10),
    )


def _pivot(index: int, price: str) -> Pivot:
    opened = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=index)
    return Pivot(
        id=f"high-{index}",
        kind=PivotKind.HIGH,
        price=Decimal(price),
        pivot_time=opened,
        confirmed_time=opened + timedelta(hours=1),
        source_index=index,
    )


def test_level_cluster_uses_quantized_median_and_symmetric_zone() -> None:
    book = LevelBook(
        "search-test",
        LevelConfig(
            zone_percent=Decimal("0.01"),
            min_bounce_percent=Decimal(0),
            min_touches=2,
            tick_size=Decimal("0.01"),
        ),
    )
    book.set_candles(
        [_candle(0, "101", "99"), _candle(1, "100.004", "99"), _candle(2, "101", "95"), _candle(3, "100.006", "99")]
    )

    level, first_created = book.add_pivot(_pivot(1, "100.004"))
    confirmed, second_created = book.add_pivot(_pivot(3, "100.006"))

    assert first_created is True
    assert second_created is False
    assert confirmed.id == level.id
    assert confirmed.state == LevelState.CONFIRMED
    assert confirmed.price == Decimal("100.01")
    assert confirmed.zone_low == confirmed.price - confirmed.price * Decimal("0.01")
    assert confirmed.zone_high == confirmed.price + confirmed.price * Decimal("0.01")
