from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from level_tester.domain.models import LevelSide


SUPPORTED_CONFIRMATION_METHODS = ("touch", "consecutive", "bounce")


class TradeSetupStatus(StrEnum):
    WAITING_CONFIRMATION = "waiting_confirmation"
    ENTRY_CONFIRMED = "entry_confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class ConfirmationConfig:
    method: str = "bounce"
    timeframe: str = "1m"
    required_bars: int = 2
    max_wait_bars: int = 15
    entry_on_next_bar: bool = True

    def __post_init__(self) -> None:
        if self.method not in SUPPORTED_CONFIRMATION_METHODS:
            raise ValueError(
                "confirmation method must be one of: "
                + ", ".join(SUPPORTED_CONFIRMATION_METHODS)
            )
        if self.timeframe not in {"1m", "5m"}:
            raise ValueError("confirmation timeframe must be 1m or 5m")
        if self.required_bars < 1 or self.max_wait_bars < self.required_bars:
            raise ValueError("confirmation bar limits are invalid")


@dataclass(slots=True)
class TradeSetup:
    id: str
    level_id: str
    side: LevelSide
    touch_time: datetime
    source_touch_time: datetime | None = None
    touch_bar_open_time: datetime | None = None
    touch_bar_close_time: datetime | None = None
    touch_refined: bool = False
    status: TradeSetupStatus = TradeSetupStatus.WAITING_CONFIRMATION
    confirmation_bars: int = 0
    confirmed_time: datetime | None = None
    entry_time: datetime | None = None
    entry_price: Decimal | None = None
    bars_waited: int = 0
    cancelled_time: datetime | None = None
    reason: str | None = None
    confirmation_method: str = "bounce"
