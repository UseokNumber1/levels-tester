from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from level_tester.domain.models import LevelSide


class TradeStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    SKIPPED = "skipped"


class BracketRule(StrEnum):
    PERCENT_FROM_ENTRY = "percent_from_entry"
    RISK_REWARD = "risk_reward"


@dataclass(frozen=True, slots=True)
class ExecutionVariant:
    id: str
    name: str
    stop_rule: BracketRule
    stop_value: Decimal
    take_rule: BracketRule
    take_value: Decimal

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise ValueError("variant id is required")
        if self.stop_value <= 0 or self.take_value <= 0:
            raise ValueError("SL/TP values must be positive")
        if self.stop_rule == BracketRule.RISK_REWARD:
            raise ValueError("stop must use percent_from_entry in the first version")
        if self.stop_rule not in (BracketRule.PERCENT_FROM_ENTRY, BracketRule.RISK_REWARD):
            raise ValueError(f"unsupported stop rule: {self.stop_rule}")
        if self.take_rule not in (BracketRule.PERCENT_FROM_ENTRY, BracketRule.RISK_REWARD):
            raise ValueError(f"unsupported take rule: {self.take_rule}")


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    stop_buffer_percent: Decimal = Decimal("0.002")
    risk_reward: Decimal = Decimal(2)
    fee_percent: Decimal = Decimal(0)
    slippage_percent: Decimal = Decimal(0)
    tick_size: Decimal = Decimal("0.01")
    intrabar_policy: str = "stop_first"
    position_sizing: str = "fixed_risk"
    risk_per_trade: Decimal = Decimal("1.0")
    variants: tuple[ExecutionVariant, ...] = ()

    def __post_init__(self) -> None:
        if self.stop_buffer_percent < 0 or self.risk_reward <= 0:
            raise ValueError("execution parameters are invalid")
        if self.fee_percent < 0 or self.slippage_percent < 0:
            raise ValueError("fees and slippage must be non-negative")
        if self.tick_size <= 0:
            raise ValueError("tick_size must be positive")
        if self.intrabar_policy not in {"stop_first", "take_first", "current_close"}:
            raise ValueError("unsupported intrabar_policy")
        if self.position_sizing not in {"fixed_risk", "fixed_notional"}:
            raise ValueError("unsupported position_sizing")
        if self.risk_per_trade <= 0:
            raise ValueError("risk_per_trade must be positive")
        seen: set[str] = set()
        for variant in self.variants:
            if variant.id in seen:
                raise ValueError(f"duplicate variant id: {variant.id}")
            seen.add(variant.id)


def round_to_tick(price: Decimal, tick_size: Decimal) -> Decimal:
    if tick_size <= 0:
        raise ValueError("tick_size must be positive")
    return (price / tick_size).quantize(Decimal(1), rounding=ROUND_HALF_UP) * tick_size


def compute_brackets(
    entry: Decimal,
    side: LevelSide,
    variant: ExecutionVariant,
    tick_size: Decimal | None = None,
) -> tuple[Decimal, Decimal]:
    """Return (stop_price, take_price) for a variant.

    Percent values in the variant are user percents (``0.2`` means 0.2%).
    ``risk_reward`` take is computed from the actual stop distance.
    """
    if variant.stop_rule != BracketRule.PERCENT_FROM_ENTRY:
        raise ValueError("stop must use percent_from_entry in the first version")
    risk_distance = entry * variant.stop_value / Decimal(100)
    stop = entry - risk_distance if side == LevelSide.SUPPORT else entry + risk_distance
    if variant.take_rule == BracketRule.PERCENT_FROM_ENTRY:
        take_distance = entry * variant.take_value / Decimal(100)
        take = entry + take_distance if side == LevelSide.SUPPORT else entry - take_distance
    elif variant.take_rule == BracketRule.RISK_REWARD:
        risk = abs(entry - stop)
        take_distance = risk * variant.take_value
        take = entry + take_distance if side == LevelSide.SUPPORT else entry - take_distance
    else:
        raise ValueError(f"unsupported take rule: {variant.take_rule}")

    if tick_size is not None and tick_size > 0:
        stop = round_to_tick(stop, tick_size)
        take = round_to_tick(take, tick_size)

    if side == LevelSide.SUPPORT:
        if not (stop < entry < take):
            raise ValueError("brackets must bracket entry for LONG")
    elif not (stop > entry > take):
        raise ValueError("brackets must bracket entry for SHORT")
    return stop, take


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
