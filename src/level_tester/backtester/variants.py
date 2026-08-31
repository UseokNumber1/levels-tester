"""Predefined execution variants for backtesting."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import yaml
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Variant:
    """One combination of SL/TP/trailing/BRE/partial parameters."""
    id: str
    name: str
    sl_pct: Decimal
    tp_rr: Optional[Decimal] = None
    tp_pct: Optional[Decimal] = None
    trailing_activation_pct: Optional[Decimal] = None
    trailing_stop_pct: Optional[Decimal] = None
    trailing_update_threshold_pct: Optional[Decimal] = None
    trailing_tp_only: bool = False
    breakeven_trigger_pct: Optional[Decimal] = None
    breakeven_lock_pct: Optional[Decimal] = None
    partial_close_pct: Optional[Decimal] = None
    partial_close_rr: Optional[Decimal] = None


# ---------------------------------------------------------------------------
# Built-in variants
# ---------------------------------------------------------------------------
BUILTIN_VARIANTS: list[Variant] = [
    # === BASIC ===
    Variant(id="sl02_rr2", name="SL 0.2% / RR 2",
            sl_pct=Decimal("0.2"), tp_rr=Decimal("2")),
    Variant(id="sl03_rr2", name="SL 0.3% / RR 2",
            sl_pct=Decimal("0.3"), tp_rr=Decimal("2")),
    Variant(id="sl05_rr2", name="SL 0.5% / RR 2",
            sl_pct=Decimal("0.5"), tp_rr=Decimal("2")),
    Variant(id="sl05_tp1", name="SL 0.5% / TP 1%",
            sl_pct=Decimal("0.5"), tp_pct=Decimal("1")),
    Variant(id="sl10_rr2", name="SL 1.0% / RR 2",
            sl_pct=Decimal("1"), tp_rr=Decimal("2")),
    Variant(id="sl10_rr3", name="SL 1.0% / RR 3",
            sl_pct=Decimal("1"), tp_rr=Decimal("3")),

    # === TRAILING ===
    Variant(id="sl02_rr2_t1", name="SL 0.2% / RR 2 / Trail 1%",
            sl_pct=Decimal("0.2"), tp_rr=Decimal("2"),
            trailing_activation_pct=Decimal("1"), trailing_stop_pct=Decimal("0.5")),
    Variant(id="sl03_rr2_t1", name="SL 0.3% / RR 2 / Trail 1%",
            sl_pct=Decimal("0.3"), tp_rr=Decimal("2"),
            trailing_activation_pct=Decimal("1"), trailing_stop_pct=Decimal("0.5")),
    Variant(id="sl05_rr2_t1", name="SL 0.5% / RR 2 / Trail 1.5%",
            sl_pct=Decimal("0.5"), tp_rr=Decimal("2"),
            trailing_activation_pct=Decimal("1.5"), trailing_stop_pct=Decimal("0.5")),
    Variant(id="sl10_rr2_t1", name="SL 1.0% / RR 2 / Trail 1.5%",
            sl_pct=Decimal("1"), tp_rr=Decimal("2"),
            trailing_activation_pct=Decimal("1.5"), trailing_stop_pct=Decimal("0.5")),

    # === TRAILING TP ONLY (no fixed TP) ===
    Variant(id="sl03_trail_only", name="SL 0.3% / Trail TP only",
            sl_pct=Decimal("0.3"), tp_rr=Decimal("999"),
            trailing_tp_only=True,
            trailing_activation_pct=Decimal("1"), trailing_stop_pct=Decimal("0.5")),

    # === BREAKEVEN ===
    Variant(id="sl03_rr2_be", name="SL 0.3% / RR 2 / BE",
            sl_pct=Decimal("0.3"), tp_rr=Decimal("2"),
            breakeven_trigger_pct=Decimal("0.8"), breakeven_lock_pct=Decimal("0.35")),
    Variant(id="sl05_rr2_be", name="SL 0.5% / RR 2 / BE",
            sl_pct=Decimal("0.5"), tp_rr=Decimal("2"),
            breakeven_trigger_pct=Decimal("1"), breakeven_lock_pct=Decimal("0.5")),

    # === PARTIAL CLOSE ===
    Variant(id="sl03_p50_rr2", name="SL 0.3% / 50%@1RR / Trail",
            sl_pct=Decimal("0.3"), tp_rr=Decimal("2"),
            partial_close_pct=Decimal("50"), partial_close_rr=Decimal("1"),
            trailing_activation_pct=Decimal("1"), trailing_stop_pct=Decimal("0.5")),
    Variant(id="sl05_p50_rr2", name="SL 0.5% / 50%@1RR / Trail",
            sl_pct=Decimal("0.5"), tp_rr=Decimal("2"),
            partial_close_pct=Decimal("50"), partial_close_rr=Decimal("1"),
            trailing_activation_pct=Decimal("1.5"), trailing_stop_pct=Decimal("0.5")),

    # === BRE + TRAILING ===
    Variant(id="sl03_rr2_be_t1", name="SL 0.3% / RR 2 / BE + Trail",
            sl_pct=Decimal("0.3"), tp_rr=Decimal("2"),
            breakeven_trigger_pct=Decimal("0.8"), breakeven_lock_pct=Decimal("0.35"),
            trailing_activation_pct=Decimal("1.5"), trailing_stop_pct=Decimal("0.5")),
]


def get_builtin(variant_ids: list[str] | None = None) -> list[Variant]:
    """Return built-in variants, optionally filtered by IDs."""
    if variant_ids is None:
        return list(BUILTIN_VARIANTS)
    return [v for v in BUILTIN_VARIANTS if v.id in variant_ids]


def load_variants_from_yaml(path: str | Path) -> list[Variant]:
    """Load custom variants from a YAML file.

    Expected format:
    ```yaml
    variants:
      - id: my_custom
        name: "My Custom Variant"
        sl_pct: 0.3
        tp_rr: 2.0
        trailing_activation_pct: 1.0
        trailing_stop_pct: 0.5
    ```
    """
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    raw_list = data.get("variants", []) if isinstance(data, dict) else data
    if not isinstance(raw_list, list):
        raise ValueError(f"expected a list of variants in {path}")

    result = []
    for raw in raw_list:
        result.append(Variant(
            id=raw["id"],
            name=raw.get("name", raw["id"]),
            sl_pct=Decimal(str(raw["sl_pct"])),
            tp_rr=Decimal(str(raw["tp_rr"])) if raw.get("tp_rr") is not None else None,
            tp_pct=Decimal(str(raw["tp_pct"])) if raw.get("tp_pct") is not None else None,
            trailing_activation_pct=_opt_dec(raw, "trailing_activation_pct"),
            trailing_stop_pct=_opt_dec(raw, "trailing_stop_pct"),
            trailing_update_threshold_pct=_opt_dec(raw, "trailing_update_threshold_pct"),
            trailing_tp_only=bool(raw.get("trailing_tp_only", False)),
            breakeven_trigger_pct=_opt_dec(raw, "breakeven_trigger_pct"),
            breakeven_lock_pct=_opt_dec(raw, "breakeven_lock_pct"),
            partial_close_pct=_opt_dec(raw, "partial_close_pct"),
            partial_close_rr=_opt_dec(raw, "partial_close_rr"),
        ))
    return result


def _opt_dec(d: dict, key: str) -> Optional[Decimal]:
    val = d.get(key)
    return Decimal(str(val)) if val is not None else None
