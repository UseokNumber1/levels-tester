from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("historical datetimes must be timezone-aware")
    return value.astimezone(UTC)


def decimal(value: Decimal | float | str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc


class PivotKind(StrEnum):
    HIGH = "high"
    LOW = "low"


class LevelSide(StrEnum):
    RESISTANCE = "resistance"
    SUPPORT = "support"


class LevelState(StrEnum):
    CREATED = "created"
    CONFIRMED = "confirmed"
    WAITING_TOUCH = "waiting_touch"
    TOUCHED = "touched"
    WAITING_ENTRY_CONFIRMATION = "waiting_entry_confirmation"
    ENTRY_CONFIRMED = "entry_confirmed"
    IN_TRADE = "in_trade"
    REBOUND = "rebound"
    BROKEN = "broken"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class Candle:
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    timeframe: str = "1h"
    is_closed: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "open_time", ensure_utc(self.open_time))
        object.__setattr__(self, "close_time", ensure_utc(self.close_time))
        for name in ("open", "high", "low", "close", "volume"):
            object.__setattr__(self, name, decimal(getattr(self, name)))
        if self.close_time <= self.open_time:
            raise ValueError("candle close_time must be after open_time")
        if not self.is_closed:
            raise ValueError("replay accepts closed candles only")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("OHLC invariant is violated")
        if self.volume < 0:
            raise ValueError("candle volume must be non-negative")


@dataclass(frozen=True, slots=True)
class Pivot:
    id: str
    kind: PivotKind
    price: Decimal
    pivot_time: datetime
    confirmed_time: datetime
    source_index: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", decimal(self.price))
        object.__setattr__(self, "pivot_time", ensure_utc(self.pivot_time))
        object.__setattr__(self, "confirmed_time", ensure_utc(self.confirmed_time))
        if self.confirmed_time < self.pivot_time:
            raise ValueError("pivot cannot be confirmed before pivot_time")


@dataclass(slots=True)
class Level:
    id: str
    side: LevelSide
    price: Decimal
    zone_low: Decimal
    zone_high: Decimal
    created_time: datetime
    confirmed_time: datetime
    source_pivot_ids: list[str] = field(default_factory=list)
    state: LevelState = LevelState.CREATED
    touched_time: datetime | None = None
    broken_time: datetime | None = None
    expired_time: datetime | None = None
    last_touch_time: datetime | None = None
    touch_count: int = 0

    def __post_init__(self) -> None:
        self.price = decimal(self.price)
        self.zone_low = decimal(self.zone_low)
        self.zone_high = decimal(self.zone_high)
        self.created_time = ensure_utc(self.created_time)
        self.confirmed_time = ensure_utc(self.confirmed_time)
        if self.zone_low > self.zone_high:
            raise ValueError("level zone_low must not exceed zone_high")


@dataclass(frozen=True, slots=True)
class LevelEvent:
    run_id: str
    sequence: int
    event_time: datetime
    event_type: str
    level_id: str
    reason: str
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_time", ensure_utc(self.event_time))
        if self.sequence < 0:
            raise ValueError("event sequence must be non-negative")


def as_json(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: as_json(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return ensure_utc(value).isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, list):
        return [as_json(item) for item in value]
    if isinstance(value, dict):
        return {key: as_json(item) for key, item in value.items()}
    return value
