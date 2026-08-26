from datetime import UTC, datetime, timedelta
from decimal import Decimal

from level_tester.domain.confirmation import ConfirmationConfig, EntryConfirmation, TradeSetupStatus
from level_tester.domain.execution import ExecutionConfig, TradeExecution, TradeStatus
from level_tester.domain.models import Candle, Level, LevelEvent, LevelSide, LevelState


def _candle(index: int, opened: str, closed: str, low: str, high: str) -> Candle:
    start = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index * 5)
    return Candle(
        start,
        start + timedelta(minutes=5),
        Decimal(opened),
        Decimal(high),
        Decimal(low),
        Decimal(closed),
        Decimal(1),
        "5m",
    )


def test_confirmation_creates_setup_and_execution_closes_at_take_profit() -> None:
    level = Level(
        id="support-1",
        side=LevelSide.SUPPORT,
        price=Decimal(100),
        zone_low=Decimal(99),
        zone_high=Decimal(101),
        created_time=datetime(2026, 1, 1, tzinfo=UTC),
        confirmed_time=datetime(2026, 1, 1, tzinfo=UTC),
        state=LevelState.TOUCHED,
        touch_count=1,
    )
    touch = LevelEvent(
        run_id="run-1",
        sequence=1,
        event_time=datetime(2026, 1, 1, 0, 30, tzinfo=UTC),
        event_type="level.touched",
        level_id=level.id,
        reason="price entered zone",
    )
    candles = [
        _candle(7, "100", "101", "99.8", "101.2"),
        _candle(8, "101", "102", "100.8", "102.2"),
        _candle(9, "102", "103", "101.9", "104"),
    ]
    confirmation = EntryConfirmation(
        "run-1", ConfirmationConfig(timeframe="5m", required_bars=2, max_wait_bars=5)
    )

    setups = confirmation.evaluate(candles, [level], [touch], 1)
    assert setups[0].status == TradeSetupStatus.ENTRY_CONFIRMED
    assert setups[0].confirmed_time == candles[1].close_time
    assert setups[0].entry_time is None

    confirmation.evaluate(candles, [level], [], 2)
    assert setups[0].entry_time == candles[2].open_time
    assert setups[0].entry_price == Decimal(102)

    execution = TradeExecution(
        "run-1", ExecutionConfig(stop_buffer_percent=Decimal("0.002"), risk_reward=Decimal(2))
    )
    trades = execution.evaluate(setups, [level], candles)

    assert trades[0].status == TradeStatus.CLOSED
    assert trades[0].exit_reason == "take_profit"
    assert trades[0].exit_price == trades[0].take_price
    assert trades[0].pnl is not None and trades[0].pnl > 0


def test_confirmation_refines_touch_from_h1_bar_to_detail_candle() -> None:
    level = Level(
        id="support-2",
        side=LevelSide.SUPPORT,
        price=Decimal(100),
        zone_low=Decimal(99),
        zone_high=Decimal(101),
        created_time=datetime(2026, 1, 1, tzinfo=UTC),
        confirmed_time=datetime(2026, 1, 1, tzinfo=UTC),
        state=LevelState.TOUCHED,
    )
    touch = LevelEvent(
        run_id="run-2",
        sequence=4,
        event_time=datetime(2026, 1, 1, 1, 0, tzinfo=UTC),
        event_type="level.touched",
        level_id=level.id,
        reason="price entered zone",
        payload={
            "candle_open_time": "2026-01-01T00:00:00+00:00",
            "candle_close_time": "2026-01-01T01:00:00+00:00",
        },
    )
    detail = [
        _candle(11, "100", "99.5", "99", "100.2"),
        _candle(12, "99.5", "100.5", "99.4", "100.8"),
        _candle(13, "100.5", "101.5", "100.4", "101.8"),
        _candle(14, "101.5", "102", "101.4", "102.2"),
    ]
    confirmation = EntryConfirmation("run-2", ConfirmationConfig(required_bars=2))

    setups = confirmation.evaluate(detail, [level], [touch], 4)

    assert setups[0].touch_refined is True
    assert setups[0].touch_time == detail[0].close_time
    assert setups[0].status == TradeSetupStatus.ENTRY_CONFIRMED
