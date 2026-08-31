from datetime import UTC, datetime, timedelta
from decimal import Decimal

from level_tester.domain.models import Candle, LevelState
from level_tester.domain.replay import ReplayConfig, ReplayEngine, ReplayWindow
from level_tester.domain.search import CausalPivotDetector, LevelConfig, PivotDetectorConfig


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
        Decimal(10),
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
    assert high_pivots[0].pivot_time == bars[2].open_time
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
    config = ReplayConfig(
        pivot=PivotDetectorConfig(wing=2, min_volume_ratio=None),
        level=LevelConfig(zone_percent=Decimal("0.008"), min_touches=1, min_bounce_percent=Decimal(0)),
    )
    first = ReplayEngine("run-a", window, bars, config=config)
    second = ReplayEngine("run-a", window, bars, config=config)
    first.step()
    snapshot = first.snapshot()
    assert len(snapshot["master_candles"]) == 1
    first.play()
    second.play()
    assert first.snapshot()["events"] == second.snapshot()["events"]
    assert any(
        level["state"] in {LevelState.WAITING_TOUCH.value, LevelState.TOUCHED.value, LevelState.CONFIRMED.value}
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
    config = ReplayConfig(
        pivot=PivotDetectorConfig(wing=2, min_volume_ratio=None),
        level=LevelConfig(zone_percent=Decimal("0.008"), min_touches=1, min_bounce_percent=Decimal(0)),
    )
    engine = ReplayEngine("run-checkpoint", window, bars, config=config)
    engine.step()
    engine.step()
    checkpoint = engine.checkpoint()
    expected = engine.snapshot()
    restored = ReplayEngine("run-checkpoint", window, bars, config=config)
    assert restored.restore(checkpoint) == expected


def test_active_level_emits_touch_and_then_breakout() -> None:
    bars = [
        candle(0, "10", "8", "9"),
        candle(1, "12", "9", "10"),
        candle(2, "11", "9", "10"),
        candle(3, "11.8", "10.5", "11"),
        candle(4, "12", "11", "11.5"),
        candle(5, "13", "11", "12"),
    ]
    window = ReplayWindow(bars[0].open_time, bars[-1].close_time, bars[0].open_time)
    config = ReplayConfig(
        pivot=PivotDetectorConfig(wing=1, min_volume_ratio=None),
        level=LevelConfig(
            zone_percent=Decimal("0.008"),
            min_bounce_percent=Decimal(0),
            min_touches=1,
            breakout="wick",
        ),
    )
    engine = ReplayEngine("run-events", window, bars, config=config)

    engine.step()
    engine.step()
    engine.step()
    approaching = engine.step()
    assert any(event["event_type"] == "level.approaching" for event in approaching["events"])
    waiting = approaching
    assert waiting["levels"][0]["state"] == LevelState.WAITING_TOUCH.value
    touched = engine.step()
    assert any(event["event_type"] == "level.touched" for event in touched["events"])
    assert touched["levels"][0]["state"] == LevelState.TOUCHED.value

    broken = engine.step()
    assert any(event["event_type"] == "level.broken" for event in broken["events"])
    assert broken["levels"][0]["state"] == LevelState.BROKEN.value
