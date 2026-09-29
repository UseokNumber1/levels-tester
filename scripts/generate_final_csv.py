#!/usr/bin/env python3
"""Generate final comprehensive CSV with all evaluated combinations."""

import json
import csv
from pathlib import Path

# Load existing grid search results
with open("grid_search_results.json") as f:
    all_results = json.load(f)

# Add the top 3 verified results with full KPI
top3 = [
    {
        "sl_pct": 1.0, "tp_pct": 5.0, "trail_activation_pct": 1.6, "trail_distance_pct": 0.6,
        "be_trigger_pct": 0.2, "be_lock_pct": 1.0, "partial_trigger_pct": 1.4, "partial_close_pct": 90.0,
        "kpi_total": 38.34, "kpi_count": 43, "kpi_decided": 41, "kpi_wins": 41, "kpi_losses": 0,
        "kpi_winrate": 100.0, "kpi_avg": 0.935, "kpi_avg_win": 0.935, "kpi_avg_loss": 0.0,
        "kpi_expectancy": 0.935, "kpi_pf": None, "kpi_max_dd": 0.0,
        "kpi_n_partial": 4, "kpi_n_be": 41, "source": "final_verification"
    },
    {
        "sl_pct": 1.0, "tp_pct": None, "trail_activation_pct": None, "trail_distance_pct": None,
        "be_trigger_pct": 0.2, "be_lock_pct": 1.0, "partial_trigger_pct": 1.4, "partial_close_pct": 90.0,
        "kpi_total": 38.34, "kpi_count": 43, "kpi_decided": 41, "kpi_wins": 41, "kpi_losses": 0,
        "kpi_winrate": 100.0, "kpi_avg": 0.935, "kpi_avg_win": 0.935, "kpi_avg_loss": 0.0,
        "kpi_expectancy": 0.935, "kpi_pf": None, "kpi_max_dd": 0.0,
        "kpi_n_partial": 4, "kpi_n_be": 41, "source": "final_verification"
    },
    {
        "sl_pct": 1.0, "tp_pct": 5.0, "trail_activation_pct": None, "trail_distance_pct": None,
        "be_trigger_pct": 0.2, "be_lock_pct": 1.0, "partial_trigger_pct": None, "partial_close_pct": None,
        "kpi_total": 36.90, "kpi_count": 43, "kpi_decided": 41, "kpi_wins": 41, "kpi_losses": 0,
        "kpi_winrate": 100.0, "kpi_avg": 0.900, "kpi_avg_win": 0.900, "kpi_avg_loss": 0.0,
        "kpi_expectancy": 0.900, "kpi_pf": None, "kpi_max_dd": 0.0,
        "kpi_n_partial": 0, "kpi_n_be": 41, "source": "final_verification"
    },
    {
        "sl_pct": 1.0, "tp_pct": 5.0, "trail_activation_pct": 1.6, "trail_distance_pct": 0.6,
        "be_trigger_pct": 0.8, "be_lock_pct": 0.35, "partial_trigger_pct": 0.8, "partial_close_pct": 50.0,
        "kpi_total": 3.78, "kpi_count": 43, "kpi_decided": 41, "kpi_wins": 28, "kpi_losses": 13,
        "kpi_winrate": 68.3, "kpi_avg": 0.092, "kpi_avg_win": 0.645, "kpi_avg_loss": -1.1,
        "kpi_expectancy": 0.092, "kpi_pf": 1.263, "kpi_max_dd": -4.91,
        "kpi_n_partial": 28, "kpi_n_be": 28, "source": "baseline"
    }
]

# Merge with existing results (deduplicate by params)
def param_key(r):
    return tuple(sorted((k, r.get(k)) for k in ["sl_pct", "tp_pct", "trail_activation_pct", "trail_distance_pct",
                                                  "be_trigger_pct", "be_lock_pct", "partial_trigger_pct", "partial_close_pct"]))

seen = set()
merged = []

# Add top3 and baseline first (priority)
for r in top3:
    key = param_key(r)
    if key not in seen:
        seen.add(key)
        merged.append(r)

# Add existing results
for r in all_results:
    key = param_key(r)
    if key not in seen:
        seen.add(key)
        merged.append(r)

# Write CSV
if merged:
    fieldnames = set()
    for r in merged:
        fieldnames.update(r.keys())
    fieldnames = sorted(fieldnames)
    
    with open("grid_search_all_combinations_final.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in merged:
            writer.writerow(r)
    
    print(f"Wrote {len(merged)} unique combinations to grid_search_all_combinations_final.csv")

# Also write a clean top-3 summary CSV
top3_clean = []
for i, r in enumerate(top3[:3], 1):
    top3_clean.append({
        "rank": i,
        "total": r["kpi_total"],
        "winrate": r["kpi_winrate"],
        "pf": r["kpi_pf"] if r["kpi_pf"] is not None else "inf",
        "max_dd": r["kpi_max_dd"],
        "expectancy": r["kpi_expectancy"],
        "wins": r["kpi_wins"],
        "decided": r["kpi_decided"],
        "n_partial": r["kpi_n_partial"],
        "n_be": r["kpi_n_be"],
        "sl_pct": r["sl_pct"],
        "tp_pct": r["tp_pct"],
        "trail_activation_pct": r["trail_activation_pct"],
        "trail_distance_pct": r["trail_distance_pct"],
        "be_trigger_pct": r["be_trigger_pct"],
        "be_lock_pct": r["be_lock_pct"],
        "partial_trigger_pct": r["partial_trigger_pct"],
        "partial_close_pct": r["partial_close_pct"],
    })

with open("top3_summary.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=top3_clean[0].keys())
    writer.writeheader()
    writer.writerows(top3_clean)

print("Wrote top3_summary.csv")