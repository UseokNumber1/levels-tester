import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from level_tester.domain.models import Candle, LevelState
from level_tester.domain.replay import ReplayConfig, ReplayEngine, ReplayWindow
from level_tester.domain.search import LevelConfig, PivotDetectorConfig

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "golden_replay.json"


def _candle(data: dict) -> Candle:
    return Candle(
        datetime.fromisoformat(data["open_time"]),
        datetime.fromisoformat(data["close_time"]),
        Decimal(data["open"]),
        Decimal(data["high"]),
        Decimal(data["low"]),
        Decimal(data["close"]),
        Decimal(data["volume"]),
        data["timeframe"],
    )


def test_golden_replay_fixture_matches_expected_causal_events() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    master = [_candle(item) for item in fixture["master_candles"]]
    detail = [_candle(item) for item in fixture["detail_candles"]]
    window = ReplayWindow(master[0].open_time, master[-1].close_time, master[0].open_time)
    config = ReplayConfig(
        pivot=PivotDetectorConfig(wing=1, min_volume_ratio=None),
        level=LevelConfig(
            zone_percent=Decimal("0.008"),
            min_bounce_percent=Decimal(0),
            min_touches=1,
            breakout="wick",
        ),
    )
    engine = ReplayEngine("golden-run", window, master, detail, config)

    snapshots = [engine.step() for _ in master]
    actual_events = [
        [event["sequence"], event["event_type"]]
        for snapshot in snapshots
        for event in snapshot["events"]
    ]

    assert actual_events == fixture["expected_events"]
    assert snapshots[0]["pivots"] == []
    assert snapshots[3]["levels"][0]["state"] == LevelState.WAITING_TOUCH.value
    assert snapshots[4]["levels"][0]["state"] == LevelState.TOUCHED.value
    assert snapshots[4]["detail_candles"] == [
        {**item, "is_closed": True}
        for item in fixture["detail_candles"][:2]
    ]
    assert all(
        datetime.fromisoformat(candle["close_time"]) <= datetime.fromisoformat("2026-01-01T05:00:00+00:00")
        for candle in snapshots[4]["detail_candles"]
    )
