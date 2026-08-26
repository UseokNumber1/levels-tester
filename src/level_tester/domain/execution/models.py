from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from level_tester.domain.models import LevelSide


class TradeStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    stop_buffer_percent: Decimal = Decimal("0.002")
    risk_reward: Decimal = Decimal(2)
    fee_percent: Decimal = Decimal(0)
    slippage_percent: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if self.stop_buffer_percent < 0 or self.risk_reward <= 0:
            raise ValueError("execution parameters are invalid")
        if self.fee_percent < 0 or self.slippage_percent < 0:
            raise ValueError("fees and slippage must be non-negative")


@dataclass(slots=True)
class Trade:
    id: str
    setup_id: str
    side: LevelSide
    entry_time: datetime
    entry_price: Decimal
    stop_price: Decimal
    take_price: Decimal
    status: TradeStatus = TradeStatus.OPEN
    exit_time: datetime | None = None
    exit_price: Decimal | None = None
    exit_reason: str | None = None
    pnl: Decimal | None = None
