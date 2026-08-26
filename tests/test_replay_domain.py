from datetime import UTC, datetime

import pytest

from level_tester.domain.replay import ReplayCursor, ReplayStatus, ReplayWindow


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
