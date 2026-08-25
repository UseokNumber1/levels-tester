from datetime import datetime, timedelta, timezone
from decimal import Decimal

from level_tester.domain.models import Candle, LevelState
from level_tester.domain.pivots import CausalPivotDetector, PivotDetectorConfig
from level_tester.domain.replay import ReplayConfig, ReplayEngine, ReplayWindow


UTC = timezone.utc


def candle(index: int, high: str, low: str, close: str | None = None) -> Candle:
    opened = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=index)
    open_price = Decimal(close or "5")
    close_price = Decimal(close or "5")
    return Candle(
        opened,
        opened + timedelta(hours=1),
        open_price,
        Decimal(high),
        Decimal(low),
        close_price,
        Decimal("10"),
    )


def test_pivot_is_not_available_before_right_wing_is_closed() -> None:
    detector = CausalPivotDetector(PivotDetectorConfig(wing=2))
    bars = [
        candle(0, "6", "4"),
        candle(1, "7", "3"),
        candle(2, "10", "2"),
        candle(3, "8", "3"),
        candle(4, "9", "4"),
    ]
    assert detector.update(bars[0]) == []
    assert detector.update(bars[1]) == []
    assert detector.update(bars[2]) == []
    assert detector.update(bars[3]) == []
    pivots = detector.update(bars[4])
    high_pivots = [pivot for pivot in pivots if pivot.kind.value == "high"]
    assert len(high_pivots) == 1
    assert high_pivots[0].pivot_time == bars[2].close_time
    assert high_pivots[0].confirmed_time == bars[4].close_time


def test_replay_is_deterministic_and_does_not_expose_future_candles() -> None:
    bars = [
        candle(0, "6", "4"),
        candle(1, "7", "3"),
        candle(2, "10", "2"),
        candle(3, "8", "3"),
        candle(4, "9", "4"),
        candle(5, "8", "4"),
    ]
    window = ReplayWindow(bars[0].open_time, bars[-1].close_time, bars[0].open_time)
    first = ReplayEngine("run-a", window, bars, config=ReplayConfig())
    second = ReplayEngine("run-a", window, bars, config=ReplayConfig())
    first.step()
    snapshot = first.snapshot()
    assert len(snapshot["master_candles"]) == 1
    first.play()
    second.play()
    assert first.snapshot()["events"] == second.snapshot()["events"]
    assert any(
        level["state"] in {LevelState.WAITING_TOUCH.value, LevelState.TOUCHED.value}
        for level in first.snapshot()["levels"]
    )


def test_equal_highs_keep_first_extreme() -> None:
    detector = CausalPivotDetector(PivotDetectorConfig(wing=1))
    bars = [candle(0, "8", "4"), candle(1, "10", "3"), candle(2, "10", "4"), candle(3, "8", "4")]
    pivots = detector.update(bars[0]) + detector.update(bars[1]) + detector.update(bars[2])
    assert [pivot.source_index for pivot in pivots if pivot.kind.value == "high"] == [1]


def test_checkpoint_restore_rebuilds_the_same_state() -> None:
    bars = [
        candle(0, "6", "4"),
        candle(1, "7", "3"),
        candle(2, "10", "2"),
        candle(3, "8", "3"),
        candle(4, "9", "4"),
        candle(5, "8", "4"),
    ]
    window = ReplayWindow(bars[0].open_time, bars[-1].close_time, bars[0].open_time)
    engine = ReplayEngine("run-checkpoint", window, bars)
    engine.step()
    engine.step()
    checkpoint = engine.checkpoint()
    expected = engine.snapshot()
    restored = ReplayEngine("run-checkpoint", window, bars)
    assert restored.restore(checkpoint) == expected
