#!/usr/bin/env python3
"""Find top 3 distinct configurations."""

import requests
import json

BASE_URL = "http://127.0.0.1:8080"
SESSION_ID = "tm1s_4"

# Test more variations around the best area
variations = [
    # Best so far: partial_close=70
    {"sl_pct": 1.0, "tp_pct": 5.0, "trail_activation_pct": 1.6, "trail_distance_pct": 0.6,
     "be_trigger_pct": 0.1, "be_lock_pct": 1.0, "partial_trigger_pct": 1.4, "partial_close_pct": 70.0},
    {"sl_pct": 1.0, "tp_pct": 5.0, "trail_activation_pct": 1.6, "trail_distance_pct": 0.6,
     "be_trigger_pct": 0.2, "be_lock_pct": 1.0, "partial_trigger_pct": 1.4, "partial_close_pct": 70.0},
    {"sl_pct": 1.0, "tp_pct": 5.0, "trail_activation_pct": 1.6, "trail_distance_pct": 0.6,
     "be_trigger_pct": 0.3, "be_lock_pct": 1.0, "partial_trigger_pct": 1.4, "partial_close_pct": 70.0},
    {"sl_pct": 1.0, "tp_pct": 5.0, "trail_activation_pct": 1.6, "trail_distance_pct": 0.6,
     "be_trigger_pct": 0.2, "be_lock_pct": 1.0, "partial_trigger_pct": 1.3, "partial_close_pct": 70.0},
    {"sl_pct": 1.0, "tp_pct": 5.0, "trail_activation_pct": 1.6, "trail_distance_pct": 0.6,
     "be_trigger_pct": 0.2, "be_lock_pct": 1.0, "partial_trigger_pct": 1.5, "partial_close_pct": 70.0},
    
    # Without trail
    {"sl_pct": 1.0, "tp_pct": 5.0, "trail_activation_pct": None, "trail_distance_pct": None,
     "be_trigger_pct": 0.2, "be_lock_pct": 1.0, "partial_trigger_pct": 1.4, "partial_close_pct": 70.0},
    {"sl_pct": 1.0, "tp_pct": None, "trail_activation_pct": None, "trail_distance_pct": None,
     "be_trigger_pct": 0.2, "be_lock_pct": 1.0, "partial_trigger_pct": 1.4, "partial_close_pct": 70.0},
    
    # Without TP
    {"sl_pct": 1.0, "tp_pct": None, "trail_activation_pct": 1.6, "trail_distance_pct": 0.6,
     "be_trigger_pct": 0.2, "be_lock_pct": 1.0, "partial_trigger_pct": 1.4, "partial_close_pct": 70.0},
    
    # Different partial_close
    {"sl_pct": 1.0, "tp_pct": 5.0, "trail_activation_pct": 1.6, "trail_distance_pct": 0.6,
     "be_trigger_pct": 0.2, "be_lock_pct": 1.0, "partial_trigger_pct": 1.4, "partial_close_pct": 60.0},
    {"sl_pct": 1.0, "tp_pct": 5.0, "trail_activation_pct": 1.6, "trail_distance_pct": 0.6,
     "be_trigger_pct": 0.2, "be_lock_pct": 1.0, "partial_trigger_pct": 1.4, "partial_close_pct": 80.0},
    {"sl_pct": 1.0, "tp_pct": 5.0, "trail_activation_pct": 1.6, "trail_distance_pct": 0.6,
     "be_trigger_pct": 0.2, "be_lock_pct": 1.0, "partial_trigger_pct": 1.4, "partial_close_pct": 90.0},
    
    # be_lock_pct variations
    {"sl_pct": 1.0, "tp_pct": 5.0, "trail_activation_pct": 1.6, "trail_distance_pct": 0.6,
     "be_trigger_pct": 0.2, "be_lock_pct": 0.9, "partial_trigger_pct": 1.4, "partial_close_pct": 70.0},
    {"sl_pct": 1.0, "tp_pct": 5.0, "trail_activation_pct": 1.6, "trail_distance_pct": 0.6,
     "be_trigger_pct": 0.2, "be_lock_pct": 0.8, "partial_trigger_pct": 1.4, "partial_close_pct": 70.0},
    
    # SL variations
    {"sl_pct": 0.8, "tp_pct": 5.0, "trail_activation_pct": 1.6, "trail_distance_pct": 0.6,
     "be_trigger_pct": 0.2, "be_lock_pct": 1.0, "partial_trigger_pct": 1.4, "partial_close_pct": 70.0},
    {"sl_pct": 1.2, "tp_pct": 5.0, "trail_activation_pct": 1.6, "trail_distance_pct": 0.6,
     "be_trigger_pct": 0.2, "be_lock_pct": 1.0, "partial_trigger_pct": 1.4, "partial_close_pct": 70.0},
]

def normalize_params(params):
    p = dict(params)
    trail_on = p.get("trail_activation_pct") not in (None, 0, "") and p.get("trail_distance_pct") not in (None, 0, "")
    be_on = p.get("be_trigger_pct") not in (None, 0, "")
    part_on = p.get("partial_trigger_pct") not in (None, 0, "") and p.get("partial_close_pct") not in (None, 0, "")
    if not trail_on:
        p["trail_activation_pct"] = None
        p["trail_distance_pct"] = None
    if not be_on:
        p["be_trigger_pct"] = None
    if not part_on:
        p["partial_trigger_pct"] = None
        p["partial_close_pct"] = None
    if p.get("tp_pct") in (0, ""):
        p["tp_pct"] = None
    return p

results = []
for v in variations:
    v_norm = normalize_params(v)
    payload = {"session_id": SESSION_ID, **v_norm}
    try:
        resp = requests.post(f"{BASE_URL}/api/hourbounce/report-tm1/recalc", json=payload, timeout=120)
        result = resp.json()
        if "kpi" not in result:
            print(f"ERROR: {result}")
            continue
        kpi = result["kpi"]
        params_str = " ".join(f"{k}={v}" for k, v in v_norm.items() if v is not None)
        pf_str = f"{kpi['pf']:.3f}" if kpi['pf'] is not None else "inf"
        print(f"Total: {kpi['total']:>6.2f} | WR: {kpi['winrate']:>5.1f} | PF: {pf_str:>6} | DD: {kpi['max_dd']:>6.2f} | EV: {kpi['expectancy']:>5.3f} | {params_str}")
        results.append((kpi['total'], v_norm, kpi))
    except Exception as e:
        print(f"Exception for {v_norm}: {e}")

# Sort and show top 10
results.sort(key=lambda x: x[0], reverse=True)
print("\n\nTOP 10:")
for i, (total, params, kpi) in enumerate(results[:10]):
    params_str = " ".join(f"{k}={v}" for k, v in params.items() if v is not None)
    pf_str = f"{kpi['pf']:.3f}" if kpi['pf'] is not None else "inf"
    print(f"{i+1}. Total: {total:.2f} | WR: {kpi['winrate']:.1f} | PF: {pf_str} | DD: {kpi['max_dd']:.2f} | EV: {kpi['expectancy']:.3f} | {params_str}")