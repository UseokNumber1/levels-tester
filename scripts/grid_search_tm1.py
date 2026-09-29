#!/usr/bin/env python3
"""
Full grid search for TM1 on September 2026 traded signals.
Stages: A (1D sweeps), B (2D heatmaps), C (coordinate descent), D (final verification).
"""

import json
import requests
import time
import csv
from dataclasses import dataclass, asdict
from typing import Any
from pathlib import Path

BASE_URL = "http://127.0.0.1:8080"
SESSION_ID = "tm1s_3"

# Baseline parameters (PGv2 defaults)
BASELINE = {
    "sl_pct": 1.0,
    "tp_pct": 5.0,
    "trail_activation_pct": 1.6,
    "trail_distance_pct": 0.6,
    "be_trigger_pct": 0.8,
    "be_lock_pct": 0.35,
    "partial_trigger_pct": 0.8,
    "partial_close_pct": 50.0,
}

# Parameter ranges from the prompt (step 0.1)
PARAM_RANGES = {
    "sl_pct": {"min": 0.05, "max": 3.0, "step": 0.1, "start": 0.1},  # 0.05, 0.1, 0.2..3.0
    "tp_pct": {"min": 0.1, "max": 5.0, "step": 0.1, "include_none": True},
    "trail_activation_pct": {"min": 0.1, "max": 5.0, "step": 0.1, "include_none": True},
    "trail_distance_pct": {"min": 0.1, "max": 5.0, "step": 0.1, "include_none": True},
    "be_trigger_pct": {"min": 0.1, "max": 5.0, "step": 0.1, "include_none": True},
    "be_lock_pct": {"min": 0.0, "max": 1.0, "step": 0.1, "include_1": True},
    "partial_trigger_pct": {"min": 0.1, "max": 5.0, "step": 0.1, "include_none": True},
    "partial_close_pct": {"min": 1.0, "max": 90.0, "step": 0.1},
}

# Heatmap pairs from prompt
HEATMAP_PAIRS = [
    ("be_trigger_pct", "be_lock_pct"),
    ("trail_activation_pct", "trail_distance_pct"),
    ("sl_pct", "tp_pct"),
    ("partial_trigger_pct", "partial_close_pct"),
]

ALL_PARAMS = list(PARAM_RANGES.keys())
RESULTS_FILE = Path("grid_search_results.json")
CSV_FILE = Path("grid_search_all_combinations.csv")

all_results = []


def recalc(params: dict[str, Any]) -> dict[str, Any]:
    """Run recalc with given parameters."""
    payload = {"session_id": SESSION_ID, **params}
    resp = requests.post(f"{BASE_URL}/api/hourbounce/report-tm1/recalc", json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()


def heatmap(x_key: str, y_key: str, x_from: float, x_to: float, x_step: float,
            y_from: float, y_to: float, y_step: float, base_params: dict[str, Any]) -> dict[str, Any]:
    """Run heatmap job and wait for completion."""
    payload = {
        "session_id": SESSION_ID,
        "x_key": x_key,
        "x_from": x_from,
        "x_to": x_to,
        "x_step": x_step,
        "y_key": y_key,
        "y_from": y_from,
        "y_to": y_to,
        "y_step": y_step,
        "base_params": base_params,
    }
    resp = requests.post(f"{BASE_URL}/api/hourbounce/report-tm1/heatmap", json=payload, timeout=10)
    resp.raise_for_status()
    job = resp.json()
    job_id = job["job_id"]

    # Poll for completion
    while True:
        time.sleep(2)
        status_resp = requests.get(f"{BASE_URL}/api/hourbounce/report-tm1/heatmap/status/{job_id}")
        status_resp.raise_for_status()
        status = status_resp.json()
        if status["status"] == "completed":
            break
        elif status["status"] == "failed":
            raise RuntimeError(f"Heatmap job failed: {status.get('error')}")

    result_resp = requests.get(f"{BASE_URL}/api/hourbounce/report-tm1/heatmap/result/{job_id}")
    result_resp.raise_for_status()
    return result_resp.json()


def record_result(params: dict[str, Any], kpi: dict[str, Any], source: str):
    """Record a result from recalc or heatmap cell."""
    row = {**params, **{f"kpi_{k}": v for k, v in kpi.items() if k not in ("equity", "hist", "outcomes", "exits")},
           "source": source}
    all_results.append(row)


def param_key(params: dict[str, Any]) -> str:
    """Create a unique key for parameter combination."""
    return json.dumps(params, sort_keys=True)


def normalize_params(params: dict[str, Any]) -> dict[str, Any]:
    """Normalize params like _tm1_normalize_params."""
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


def generate_values(param_name: str) -> list:
    """Generate all values for a parameter per the grid spec."""
    spec = PARAM_RANGES[param_name]
    values = []

    if param_name == "sl_pct":
        # 0.05, 0.1, 0.2..3.0
        values.append(0.05)
        v = 0.1
        while v <= spec["max"] + 1e-9:
            values.append(round(v, 4))
            v += spec["step"]
    elif param_name == "tp_pct":
        if spec.get("include_none"):
            values.append(None)
        v = spec["min"]
        while v <= spec["max"] + 1e-9:
            values.append(round(v, 4))
            v += spec["step"]
    elif param_name == "trail_activation_pct":
        if spec.get("include_none"):
            values.append(None)
        v = spec["min"]
        while v <= spec["max"] + 1e-9:
            values.append(round(v, 4))
            v += spec["step"]
    elif param_name == "trail_distance_pct":
        if spec.get("include_none"):
            values.append(None)
        v = spec["min"]
        while v <= spec["max"] + 1e-9:
            values.append(round(v, 4))
            v += spec["step"]
    elif param_name == "be_trigger_pct":
        if spec.get("include_none"):
            values.append(None)
        v = spec["min"]
        while v <= spec["max"] + 1e-9:
            values.append(round(v, 4))
            v += spec["step"]
    elif param_name == "be_lock_pct":
        v = spec["min"]
        while v <= 0.9 + 1e-9:
            values.append(round(v, 4))
            v += spec["step"]
        if spec.get("include_1"):
            values.append(1.0)
    elif param_name == "partial_trigger_pct":
        if spec.get("include_none"):
            values.append(None)
        v = spec["min"]
        while v <= spec["max"] + 1e-9:
            values.append(round(v, 4))
            v += spec["step"]
    elif param_name == "partial_close_pct":
        v = spec["min"]
        while v <= spec["max"] + 1e-9:
            values.append(round(v, 4))
            v += spec["step"]

    return values


def run_stage_a():
    """Stage A: 1D sweep of each parameter."""
    print("=" * 60)
    print("STAGE A: 1D Parameter Sweeps")
    print("=" * 60)

    for param in ALL_PARAMS:
        print(f"\n--- Sweeping {param} ---")
        values = generate_values(param)
        best_total = -float("inf")
        best_val = None

        for val in values:
            params = BASELINE.copy()
            params[param] = val
            params = normalize_params(params)

            try:
                result = recalc(params)
                kpi = result["kpi"]
                total = kpi["total"]
                record_result(params, kpi, f"1d_{param}")

                if total > best_total:
                    best_total = total
                    best_val = val

                print(f"  {param}={val}: total={total:.2f}, wr={kpi['winrate']}, pf={kpi['pf']}, dd={kpi['max_dd']}")
            except Exception as e:
                print(f"  {param}={val}: ERROR - {e}")

        print(f"  BEST {param}={best_val} with total={best_total:.2f}")


def run_heatmap_subgrid(x_key: str, y_key: str, x_from: float, x_to: float, x_step: float,
                        y_from: float, y_to: float, y_step: float, base_params: dict[str, Any]):
    """Run a single heatmap subgrid and record results."""
    try:
        result = heatmap(x_key, y_key, x_from, x_to, x_step, y_from, y_to, y_step, base_params)
        grid = result["grid"]
        x_vals_res = result["x_vals"]
        y_vals_res = result["y_vals"]

        best_total = -float("inf")
        best_x = best_y = None

        for i, y in enumerate(y_vals_res):
            for j, x in enumerate(x_vals_res):
                total = grid[i][j]
                params = base_params.copy()
                params[x_key] = x
                params[y_key] = y
                params = normalize_params(params)
                kpi = {"total": total}
                record_result(params, kpi, f"heatmap_{x_key}_{y_key}")

                if total > best_total:
                    best_total = total
                    best_x = x
                    best_y = y

        print(f"    Subgrid best: {x_key}={best_x}, {y_key}={best_y} total={best_total:.2f}")
        return best_total, best_x, best_y
    except Exception as e:
        print(f"    Subgrid failed: {e}")
        return None, None, None


def run_stage_b():
    """Stage B: 2D heatmaps for parameter pairs, split into sub-grids <= 400 cells."""
    print("\n" + "=" * 60)
    print("STAGE B: 2D Heatmaps")
    print("=" * 60)

    max_cells = 400

    for x_key, y_key in HEATMAP_PAIRS:
        print(f"\n--- Heatmap {x_key} x {y_key} ---")
        x_spec = PARAM_RANGES[x_key]
        y_spec = PARAM_RANGES[y_key]

        # Generate all values
        x_vals = generate_values(x_key)
        y_vals = generate_values(y_key)

        # Remove None values for heatmap
        x_vals = [v for v in x_vals if v is not None]
        y_vals = [v for v in y_vals if v is not None]

        # Calculate step sizes needed to keep grid <= 400
        x_step = x_spec["step"]
        y_step = y_spec["step"]

        # If grid too large, we'll split into sub-grids
        if len(x_vals) * len(y_vals) > max_cells:
            # Determine how many chunks we need in each dimension
            n_chunks_x = max(1, int((len(x_vals) * len(y_vals) / max_cells) ** 0.5) + 1)
            n_chunks_y = max(1, int((len(x_vals) * len(y_vals) / max_cells) ** 0.5) + 1)

            chunk_size_x = max(1, len(x_vals) // n_chunks_x + 1)
            chunk_size_y = max(1, len(y_vals) // n_chunks_y + 1)

            print(f"  Grid {len(x_vals)}x{len(y_vals)}={len(x_vals)*len(y_vals)} > 400, splitting into ~{n_chunks_x}x{n_chunks_y} subgrids")

            base = BASELINE.copy()
            base = normalize_params(base)

            overall_best = -float("inf")
            overall_best_x = overall_best_y = None

            for xi in range(0, len(x_vals), chunk_size_x):
                x_chunk = x_vals[xi:xi + chunk_size_x]
                for yi in range(0, len(y_vals), chunk_size_y):
                    y_chunk = y_vals[yi:yi + chunk_size_y]

                    x_from, x_to = x_chunk[0], x_chunk[-1]
                    y_from, y_to = y_chunk[0], y_chunk[-1]

                    # Adjust step to match our grid
                    actual_x_step = x_step if len(x_chunk) > 1 else 1.0
                    actual_y_step = y_step if len(y_chunk) > 1 else 1.0

                    print(f"    Subgrid: {x_key} {x_from}..{x_to}, {y_key} {y_from}..{y_to}")

                    best_total, best_x, best_y = run_heatmap_subgrid(
                        x_key, y_key, x_from, x_to, actual_x_step,
                        y_from, y_to, actual_y_step, base
                    )

                    if best_total is not None and best_total > overall_best:
                        overall_best = best_total
                        overall_best_x = best_x
                        overall_best_y = best_y

            print(f"  OVERALL BEST: {x_key}={overall_best_x}, {y_key}={overall_best_y} total={overall_best:.2f}")
        else:
            # Small enough for single heatmap
            base = BASELINE.copy()
            base = normalize_params(base)
            print(f"  Single grid: {len(x_vals)}x{len(y_vals)}={len(x_vals)*len(y_vals)} cells")

            best_total, best_x, best_y = run_heatmap_subgrid(
                x_key, y_key, x_vals[0], x_vals[-1], x_step,
                y_vals[0], y_vals[-1], y_step, base
            )
            print(f"  BEST: {x_key}={best_x}, {y_key}={best_y} total={best_total:.2f}")


def run_stage_c():
    """Stage C: Coordinate descent on top candidates."""
    print("\n" + "=" * 60)
    print("STAGE C: Coordinate Descent")
    print("=" * 60)

    # Get top candidates from all results so far
    sorted_results = sorted(all_results, key=lambda r: r.get("kpi_total", -999), reverse=True)
    top_candidates = sorted_results[:20]

    print(f"Top {len(top_candidates)} candidates from A+B:")
    for i, r in enumerate(top_candidates):
        params_str = ", ".join(f"{k}={r.get(k)}" for k in ALL_PARAMS)
        print(f"  {i+1}. total={r.get('kpi_total', 0):.2f} [{params_str}]")

    # For each top candidate, do coordinate descent
    improved = []
    for idx, cand in enumerate(top_candidates):
        print(f"\n  Candidate {idx+1}: total={cand.get('kpi_total', 0):.2f}")
        current = {k: cand.get(k) for k in ALL_PARAMS}
        current = {k: v for k, v in current.items() if k in ALL_PARAMS}

        improved_params, improved_total = coordinate_descent(current)
        improved.append((improved_params, improved_total))

    # Sort by total
    improved.sort(key=lambda x: x[1], reverse=True)
    print("\nAfter coordinate descent:")
    for i, (params, total) in enumerate(improved[:10]):
        params_str = ", ".join(f"{k}={params.get(k)}" for k in ALL_PARAMS)
        print(f"  {i+1}. total={total:.2f} [{params_str}]")

    return improved


def coordinate_descent(start_params: dict[str, Any], max_iter: int = 20) -> tuple[dict[str, Any], float]:
    """Run coordinate descent from starting parameters."""
    current = start_params.copy()
    current = normalize_params(current)

    # Evaluate starting point
    result = recalc(current)
    current_total = result["kpi"]["total"]
    print(f"    Start: total={current_total:.2f}")

    for iteration in range(max_iter):
        improved = False
        best_param = None
        best_val = None
        best_total = current_total

        for param in ALL_PARAMS:
            values = generate_values(param)
            # Only test neighbors around current value
            current_val = current.get(param)
            if current_val is None:
                continue

            # Find index of current value
            try:
                idx = values.index(current_val)
            except ValueError:
                continue

            # Test neighbors
            for neighbor_idx in [idx - 1, idx + 1]:
                if 0 <= neighbor_idx < len(values):
                    val = values[neighbor_idx]
                    test_params = current.copy()
                    test_params[param] = val
                    test_params = normalize_params(test_params)

                    try:
                        result = recalc(test_params)
                        total = result["kpi"]["total"]
                        if total > best_total:
                            best_total = total
                            best_param = param
                            best_val = val
                            improved = True
                    except Exception:
                        pass

        if improved:
            current[best_param] = best_val
            current = normalize_params(current)
            current_total = best_total
            print(f"    Iter {iteration+1}: {best_param}={best_val} -> total={current_total:.2f}")
        else:
            print(f"    Converged at iteration {iteration+1}")
            break

    return current, current_total


def run_stage_d(top_candidates: list[tuple[dict[str, Any], float]]):
    """Stage D: Final verification of top 3."""
    print("\n" + "=" * 60)
    print("STAGE D: Final Verification of Top 3")
    print("=" * 60)

    top_3 = top_candidates[:3]

    for rank, (params, expected_total) in enumerate(top_3, 1):
        print(f"\n  Rank {rank}: Expected total={expected_total:.2f}")
        params = normalize_params(params)

        # Run recalc 3 times to verify determinism
        totals = []
        for run in range(3):
            result = recalc(params)
            total = result["kpi"]["total"]
            totals.append(total)
            print(f"    Run {run+1}: total={total:.2f}")

        # Check stability: neighbors ±0.1
        print(f"    Stability check (±0.1 neighbors):")
        for param in ALL_PARAMS:
            current_val = params.get(param)
            if current_val is None:
                continue
            values = generate_values(param)
            try:
                idx = values.index(current_val)
            except ValueError:
                continue

            for neighbor_idx in [idx - 1, idx + 1]:
                if 0 <= neighbor_idx < len(values):
                    val = values[neighbor_idx]
                    test_params = params.copy()
                    test_params[param] = val
                    test_params = normalize_params(test_params)
                    result = recalc(test_params)
                    neighbor_total = result["kpi"]["total"]
                    diff = neighbor_total - expected_total
                    status = "OK" if diff >= -0.5 else "DROP"
                    print(f"      {param}={val}: total={neighbor_total:.2f} (Δ={diff:+.2f}) {status}")

        avg_total = sum(totals) / len(totals)
        print(f"    Average total: {avg_total:.2f}")


def save_results():
    """Save all results to JSON and CSV."""
    print("\n" + "=" * 60)
    print("SAVING RESULTS")
    print("=" * 60)

    # Save JSON
    with open(RESULTS_FILE, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"Saved {len(all_results)} results to {RESULTS_FILE}")

    # Save CSV
    if all_results:
        fieldnames = set()
        for r in all_results:
            fieldnames.update(r.keys())
        fieldnames = sorted(fieldnames)

        with open(CSV_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in all_results:
                writer.writerow(r)
        print(f"Saved CSV to {CSV_FILE}")


def print_top3_table(top_candidates: list[tuple[dict[str, Any], float]]):
    """Print the final top-3 table."""
    print("\n" + "=" * 60)
    print("FINAL TOP-3 TABLE")
    print("=" * 60)
    print(f"{'Rank':<5} {'Σ PnL':<8} {'WR':<6} {'PF':<6} {'maxDD':<7} {'EV':<7}  Parameters")
    for rank, (params, total) in enumerate(top_candidates[:3], 1):
        # Find full KPI for this param set
        kpi = next((r for r in all_results if all(r.get(k) == params.get(k) for k in ALL_PARAMS)), {})
        wr = kpi.get("kpi_winrate", 0)
        pf = kpi.get("kpi_pf", 0)
        dd = kpi.get("kpi_max_dd", 0)
        ev = kpi.get("kpi_expectancy", 0)

        params_str = " ".join(f"{k}={params.get(k)}" for k in ALL_PARAMS)
        print(f"{rank:<5} {total:<8.2f} {wr:<6.1f} {pf:<6.3f} {dd:<7.2f} {ev:<7.3f}  {params_str}")


def main():
    print("Starting TM1 Grid Search on September 2026 signals")
    print(f"Session: {SESSION_ID}")
    print(f"Baseline: {BASELINE}")

    # Run baseline first
    print("\nRunning baseline...")
    result = recalc(BASELINE)
    kpi = result["kpi"]
    record_result(BASELINE, kpi, "baseline")
    print(f"Baseline: total={kpi['total']:.2f}, wr={kpi['winrate']}, pf={kpi['pf']}, dd={kpi['max_dd']}")

    # Run stages
    run_stage_a()
    run_stage_b()
    improved = run_stage_c()
    run_stage_d(improved)

    # Save and print results
    save_results()
    print_top3_table(improved)

    print("\nDone!")


if __name__ == "__main__":
    main()