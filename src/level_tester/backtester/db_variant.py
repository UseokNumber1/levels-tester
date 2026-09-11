"""Resolve per-signal 'as traded in DB' execution params (DB baseline variant)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from level_tester.backtester.signal_reader import SignalEntry
from level_tester.backtester.variants import Variant

DB_VARIANT_ID = "__DB__"
DB_VARIANT_NAME = "DB · как в базе"

PGV2_CONFIG_CANDIDATES = (
    Path(r"E:\Pyton_project\precision_grid_v2\config\config.yaml"),
    Path(__file__).resolve().parents[4] / "config" / "pgv2_live.yaml",
)


@lru_cache(maxsize=1)
def load_pgv2_live() -> dict[str, Any]:
    """Read live P_G_V2 trading_settings as fallback. Never raises."""
    for path in PGV2_CONFIG_CANDIDATES:
        try:
            if not path.exists():
                continue
            import yaml  # type: ignore

            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            ts = data.get("trading_settings", {}) if isinstance(data, dict) else {}
            if ts:
                return dict(ts)
        except Exception:
            continue
    # Safe defaults matching current live config
    return {
        "stop_loss_pct": 1.0,
        "take_profit_method": "rr_ratio",
        "take_profit_pct": 3.0,
        "rr_ratio": 3.0,
        "breakeven_enabled": True,
        "breakeven_trigger_pct": 0.9,
        "breakeven_profit_pct": 0.35,
        "breakeven_fix_enabled": True,
        "breakeven_fix_pct": 50.0,
        "trailing_stop_enabled": True,
        "trailing_activation_pct": 1.0,
        "trailing_stop_pct": 0.6,
        "trailing_update_threshold_pct": 0.5,
        "trailing_tp_only": False,
    }


@dataclass(slots=True)
class DBResolved:
    """Concrete per-signal params + source tags for UI badges."""
    variant: Variant
    sources: dict[str, str] = field(default_factory=dict)  # field -> signal|metadata|live|computed
    entry_price: float = 0.0
    sl_price: float = 0.0
    tp_price: Optional[float] = None


def _first_tp(entry: SignalEntry) -> Optional[float]:
    if not entry.take_profits:
        return None
    raw = entry.take_profits
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(data, list) and data:
            return float(data[0])
        if isinstance(data, (int, float)):
            return float(data)
    except Exception:
        pass
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def resolve_db_variant(signal: SignalEntry, live: dict[str, Any] | None = None) -> DBResolved:
    """Build Variant equivalent of 'how it traded in DB' for one signal."""
    live = live if live is not None else load_pgv2_live()
    sources: dict[str, str] = {}
    entry = float(signal.entry_price or 0.0)

    def pick(value: Any, fallback_key: str, tag_signal: str = "signal") -> tuple[Any, str]:
        if value is not None and value != "":
            return value, tag_signal
        return live.get(fallback_key), "live"

    # --- SL ---
    sl_price: Optional[float] = None
    sl_src = "live"
    if signal.stop_loss:
        sl_price = float(signal.stop_loss)
        sl_src = "signal" if signal.source == "trading" else "metadata"
    else:
        sl_pct_live = float(live.get("stop_loss_pct", 1.0))
        sl_price = entry * (1 - sl_pct_live / 100) if signal.side == "LONG" else entry * (1 + sl_pct_live / 100)
    sl_pct = abs(entry - sl_price) / entry * 100 if entry else float(live.get("stop_loss_pct", 1.0))
    sources["sl"] = sl_src

    # --- TP / trailing_tp_only ---
    tp_only_val, tp_only_src = pick(signal.trailing_tp_only, "trailing_tp_only")
    tp_only = bool(tp_only_val)
    sources["trailing_tp_only"] = tp_only_src if signal.trailing_tp_only is not None else "live"
    tp_price = _first_tp(signal)
    tp_src = "signal" if tp_price is not None else "live"
    tp_rr: Optional[Decimal] = None
    tp_pct: Optional[Decimal] = None
    if tp_only:
        tp_rr = Decimal("999")
        tp_price = None
    elif tp_price is None:
        method = str(live.get("take_profit_method", "rr_ratio"))
        if method == "percent":
            tp_pct = Decimal(str(live.get("take_profit_pct", 3.0)))
            tp_price = entry * (1 + float(tp_pct) / 100) if signal.side == "LONG" else entry * (1 - float(tp_pct) / 100)
        else:
            tp_rr = Decimal(str(signal.rr_ratio if signal.rr_ratio else live.get("rr_ratio", 3.0)))
            tp_price = entry + (entry - sl_price) * float(tp_rr) * (1 if signal.side == "LONG" else -1)
    else:
        # Absolute TP from DB -> express as tp_pct for the engine
        pct = abs(tp_price - entry) / entry * 100 if entry else 0
        tp_pct = Decimal(str(round(pct, 6)))
        tp_rr = None
    sources["tp"] = tp_src

    # --- BE ---
    be_en, be_en_src = pick(signal.breakeven_enabled, "breakeven_enabled")
    be_tr, be_tr_src = pick(signal.breakeven_trigger_pct, "breakeven_trigger_pct")
    be_pr, be_pr_src = pick(signal.breakeven_profit_pct, "breakeven_profit_pct")
    fix_en, fix_en_src = pick(signal.breakeven_fix_enabled, "breakeven_fix_enabled")
    fix_pct, fix_src = pick(signal.breakeven_fix_pct, "breakeven_fix_pct")
    be_enabled = bool(be_en)
    fix_enabled = bool(fix_en)
    tag = "signal" if signal.source == "trading" else "metadata"
    for k, v in (("be_en", be_en_src), ("be_tr", be_tr_src), ("be_pr", be_pr_src),
                 ("fix_en", fix_en_src), ("fix", fix_src)):
        sources[k] = tag if v != "live" else "live"

    # --- Trail ---
    tr_en, tr_en_src = pick(signal.trailing_stop_enabled, "trailing_stop_enabled")
    tr_act, tr_act_src = pick(signal.trailing_activation_pct, "trailing_activation_pct")
    tr_dist, tr_dist_src = pick(signal.trailing_stop_pct, "trailing_stop_pct")
    tr_upd, tr_upd_src = pick(signal.trailing_update_threshold_pct, "trailing_update_threshold_pct")
    tr_enabled = bool(tr_en)
    for k, v in (("tr_en", tr_en_src), ("tr_act", tr_act_src), ("tr_dist", tr_dist_src), ("tr_upd", tr_upd_src)):
        sources[k] = tag if v != "live" else "live"

    # BE-fix in tester = partial_close at BE-trigger level:
    # target(entry + risk*RR) == entry + trigger  <=>  RR = trigger / sl_pct
    partial_pct: Optional[Decimal] = None
    partial_rr: Optional[Decimal] = None
    if be_enabled and fix_enabled and fix_pct and float(fix_pct) > 0 and be_tr and sl_pct:
        partial_pct = Decimal(str(fix_pct))
        partial_rr = Decimal(str(round(float(be_tr) / sl_pct, 6)))
        sources["fix_mode"] = "be_trigger_as_rr"
    else:
        sources["fix_mode"] = "off"

    variant = Variant(
        id=DB_VARIANT_ID,
        name=DB_VARIANT_NAME,
        sl_pct=Decimal(str(round(sl_pct, 6))),
        tp_rr=tp_rr,
        tp_pct=tp_pct,
        trailing_activation_pct=Decimal(str(tr_act)) if (tr_enabled and tr_act is not None) else None,
        trailing_stop_pct=Decimal(str(tr_dist)) if (tr_enabled and tr_dist is not None) else None,
        trailing_update_threshold_pct=Decimal(str(tr_upd)) if (tr_enabled and tr_upd is not None) else None,
        trailing_tp_only=tp_only,
        breakeven_trigger_pct=Decimal(str(be_tr)) if (be_enabled and be_tr is not None) else None,
        breakeven_lock_pct=Decimal(str(be_pr)) if (be_enabled and be_pr is not None) else None,
        partial_close_pct=partial_pct,
        partial_close_rr=partial_rr,
    )
    return DBResolved(variant=variant, sources=sources, entry_price=entry,
                      sl_price=sl_price or 0.0, tp_price=tp_price)
