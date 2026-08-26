from decimal import Decimal
from pathlib import Path

from level_tester.settings import load_replay_config


def test_default_algorithm_configuration_matches_replay_specification() -> None:
    path = Path(__file__).parents[1] / "config" / "default.yaml"

    config = load_replay_config(path)

    assert config.pivot.wing == 6
    assert config.pivot.min_volume_ratio == Decimal("0.5")
    assert config.level.zone_percent == Decimal("0.008")
    assert config.level.min_bounce_percent == Decimal("0.045")
    assert config.level.min_touches == 2
    assert config.level.breakout == "wick"
    assert config.detail_timeframe == "1m"
    assert [profile.name for profile in config.outcome_profiles] == [
        "quick_rebound",
        "patient_rebound",
    ]
