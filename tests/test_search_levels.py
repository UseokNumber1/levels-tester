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


def _pivot(index: int, price: str, kind: PivotKind = PivotKind.HIGH) -> Pivot:
    opened = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=index)
    return Pivot(
        id=f"{kind.value}-{index}",
        kind=kind,
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
        [_candle(0, "101", "99"), _candle(1, "100.006", "99"), _candle(2, "101", "95"), _candle(3, "100.004", "99")]
    )

    book.add_pivot(_pivot(1, "100.006"))
    level, _ = book.add_pivot(_pivot(3, "100.004"))

    assert len(book.levels) == 1
    assert level.state == LevelState.CONFIRMED
    assert level.price == Decimal("100.01")
    assert level.zone_low < level.price < level.zone_high


def test_first_pivot_requires_rebound_to_form_a_level() -> None:
    config = LevelConfig(
        min_bounce_percent=Decimal("0.045"),
        min_touches=2,
        tick_size=Decimal("0.01"),
        max_lookahead=50,
    )

    no_rebound = LevelBook("no-rebound", config)
    no_rebound.set_candles(
        [
            _candle(0, "101", "100"),
            _candle(1, "101", "100"),
            _candle(2, "101", "96"),
            _candle(3, "101", "100"),
        ]
    )
    level, created = no_rebound.add_pivot(_pivot(1, "100", PivotKind.LOW))
    assert level is None
    assert created is False
    assert all(lv.state != LevelState.CONFIRMED for lv in no_rebound.levels)

    with_rebound = LevelBook("with-rebound", config)
    with_rebound.set_candles(
        [
            _candle(0, "101", "100"),
            _candle(1, "101", "100"),
            _candle(2, "101", "95"),
            _candle(3, "101", "100"),
        ]
    )
    level, created = with_rebound.add_pivot(_pivot(1, "100", PivotKind.LOW))
    assert level is not None
    assert created is True
    assert level.state == LevelState.CREATED
    assert level.touch_count == 1


def test_two_touches_require_rebound_between_them() -> None:
    config = LevelConfig(
        min_bounce_percent=Decimal("0.045"),
        min_touches=2,
        tick_size=Decimal("0.01"),
        max_lookahead=50,
    )

    candles = [
        _candle(0, "101", "100"),
        _candle(1, "101", "99.3"),
        _candle(2, "101", "95"),
        _candle(3, "101", "100"),
        _candle(4, "101", "100"),
        _candle(5, "101", "100.0"),
        _candle(6, "101", "95"),
    ]

    confirmed = LevelBook("confirmed", config)
    confirmed.set_candles(candles)
    confirmed.add_pivot(_pivot(1, "99.3", PivotKind.LOW))
    level, _ = confirmed.add_pivot(_pivot(5, "100.0", PivotKind.LOW))
    assert level is not None
    assert level.state == LevelState.CONFIRMED
    assert level.touch_count == 2
    assert level.price == Decimal("99.65")

    no_inter_rebound = LevelBook("no-inter", config)
    no_inter_rebound.set_candles(
        [
            _candle(0, "101", "100"),
            _candle(1, "101", "99.3"),
            _candle(2, "101", "100"),
            _candle(3, "101", "100"),
            _candle(4, "101", "100"),
            _candle(5, "101", "100.0"),
            _candle(6, "101", "95"),
        ]
    )
    no_inter_rebound.add_pivot(_pivot(1, "99.0", PivotKind.LOW))
    level, _ = no_inter_rebound.add_pivot(_pivot(5, "100.0", PivotKind.LOW))
    assert level is not None
    assert level.state == LevelState.CREATED
    assert level.touch_count == 1


def test_level_accumulates_three_touches_with_rebounds_between_them() -> None:
    book = LevelBook(
        "acc-touches",
        LevelConfig(
            min_bounce_percent=Decimal("0.045"),
            min_touches=2,
            tick_size=Decimal("0.01"),
            max_lookahead=50,
        ),
    )
    book.set_candles(
        [
            _candle(0, "101", "100"),
            _candle(1, "101", "99.6"),
            _candle(2, "101", "94"),
            _candle(3, "101", "99.8"),
            _candle(4, "101", "94"),
            _candle(5, "101", "100.0"),
            _candle(6, "101", "94"),
        ]
    )

    book.add_pivot(_pivot(1, "99.6", PivotKind.LOW))
    book.add_pivot(_pivot(3, "99.8", PivotKind.LOW))
    level, _ = book.add_pivot(_pivot(5, "100.0", PivotKind.LOW))

    assert level is not None
    assert level.state == LevelState.CONFIRMED
    assert level.touch_count == 3
