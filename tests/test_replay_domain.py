from datetime import UTC, datetime
from decimal import Decimal

import pytest

from level_tester.domain.models import Candle
from level_tester.domain.replay import (
    ReplayConfig,
    ReplayCursor,
    ReplayEngine,
    ReplayStatus,
    ReplayWindow,
)


def test_replay_window_keeps_display_and_calculation_ranges_separate() -> None:
    display_from = datetime(2026, 1, 10, tzinfo=UTC)
    effective_to = datetime(2026, 1, 20, tzinfo=UTC)
    window = ReplayWindow(
        display_from=display_from,
        effective_to=effective_to,
        calculation_from=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert window.calculation_from < window.display_from


def test_replay_rejects_naive_cursor_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ReplayCursor(bar_time=datetime(2026, 1, 1), sequence=0)  # noqa: DTZ001


def test_replay_cursor_rejects_negative_sequence() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        ReplayCursor(bar_time=datetime(2026, 1, 1, tzinfo=UTC), sequence=-1)


def test_replay_status_has_explicit_lifecycle() -> None:
    assert ReplayStatus.READY.value == "ready"
    assert ReplayStatus.PAUSED.value == "paused"


def test_replay_exposes_selected_confirmation_methods() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    candle = Candle(
        start,
        start.replace(hour=1),
        Decimal("100"),
        Decimal("101"),
        Decimal("99"),
        Decimal("100"),
        Decimal("1"),
        "1h",
    )
    window = ReplayWindow(start, candle.close_time, start)
    config = ReplayConfig(confirmation_methods=("touch", "consecutive", "bounce"))
    engine = ReplayEngine("methods-run", window, [candle], config=config)

    assert engine.snapshot()["confirmation_methods"] == ["touch", "consecutive", "bounce"]
