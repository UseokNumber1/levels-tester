from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import NAMESPACE_URL, uuid5

from level_tester.domain.models import Candle, Level, LevelEvent, LevelSide


class OutcomeStatus(StrEnum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILURE = "failure"


@dataclass(frozen=True, slots=True)
class OutcomeProfile:
    name: str
    wait_bars: int = 3
    min_bounce_percent: Decimal = Decimal("0.001")

    def __post_init__(self) -> None:
        if not self.name or self.wait_bars < 1 or self.min_bounce_percent < 0:
            raise ValueError("invalid outcome profile")


@dataclass(slots=True)
class Outcome:
    id: str
    profile_name: str
    level_id: str
    touch_time: datetime
    status: OutcomeStatus = OutcomeStatus.PENDING
    resolved_time: datetime | None = None
    reason: str | None = None
    bars_waited: int = 0


class OutcomeEvaluator:
    def __init__(self, run_id: str, profiles: tuple[OutcomeProfile, ...]) -> None:
        self.run_id = run_id
        self.profiles = profiles
        self.outcomes: list[Outcome] = []

    def evaluate(
        self, candle: Candle, levels: list[Level], events: list[LevelEvent], sequence: int
    ) -> list[LevelEvent]:
        emitted: list[LevelEvent] = []
        touched_ids = {event.level_id for event in events if event.event_type == "level.touched"}
        for level in levels:
            if level.id in touched_ids:
                for profile in self.profiles:
                    self.outcomes.append(
                        Outcome(
                            str(
                                uuid5(
                                    NAMESPACE_URL,
                                    f"{self.run_id}:{profile.name}:{level.id}:{level.touch_count}",
                                )
                            ),
                            profile.name,
                            level.id,
                            candle.close_time,
                        )
                    )
        for outcome in self.outcomes:
            if outcome.status != OutcomeStatus.PENDING:
                continue
            level = next((item for item in levels if item.id == outcome.level_id), None)
            if level is None:
                continue
            profile = next(item for item in self.profiles if item.name == outcome.profile_name)
            outcome.bars_waited += 1
            success_price = level.price * (Decimal("1") + profile.min_bounce_percent)
            if level.side == LevelSide.RESISTANCE:
                success = candle.close <= level.price * (Decimal("1") - profile.min_bounce_percent)
            else:
                success = candle.close >= success_price
            failure = level.broken_time == candle.close_time or level.state.value == "broken"
            if success:
                outcome.status = OutcomeStatus.SUCCESS
                outcome.resolved_time = candle.close_time
                outcome.reason = "minimum rebound reached"
            elif failure or outcome.bars_waited >= profile.wait_bars:
                outcome.status = OutcomeStatus.FAILURE
                outcome.resolved_time = candle.close_time
                outcome.reason = "breakout or timeout"
            if outcome.status != OutcomeStatus.PENDING:
                emitted.append(
                    LevelEvent(
                        run_id=self.run_id,
                        sequence=sequence,
                        event_time=candle.close_time,
                        event_type=f"outcome.{outcome.status.value}",
                        level_id=level.id,
                        reason=outcome.reason or outcome.status.value,
                        payload={"profile": profile.name, "outcome_id": outcome.id},
                    )
                )
        return emitted
