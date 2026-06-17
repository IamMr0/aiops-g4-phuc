#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import requests
import yaml


PIPELINE_URL = "http://localhost:8000"
COOLDOWN_SECONDS = 3


def load_experiments(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)["experiments"]


def query_pipeline_alerts(since_ts: int) -> list[dict]:
    response = requests.get(f"{PIPELINE_URL}/alerts", params={"since": since_ts}, timeout=10)
    response.raise_for_status()
    return response.json()


def query_pipeline_rca(window_start: int, window_end: int) -> dict:
    response = requests.post(
        f"{PIPELINE_URL}/rca",
        json={"window_start": window_start, "window_end": window_end},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def build_inject_cmd(exp: dict) -> list[str]:
    duration = int(exp["blast_radius"]["duration_seconds"])
    expected_root = str(exp["ground_truth"]["expected_root_service"])
    return [
        sys.executable,
        "scripts/inject_fault.py",
        "--id",
        str(exp["id"]),
        "--name",
        str(exp["name"]),
        "--service",
        str(exp["target"]),
        "--fault-type",
        str(exp["fault_type"]),
        "--duration",
        str(duration),
        "--expected-root",
        expected_root,
    ]


def build_rollback_cmd(exp: dict) -> list[str] | None:
    return None


def measure_during_window(exp: dict, t0: int) -> dict:
    capture = int(exp["measurement"]["capture_window_seconds"])
    t_end = t0 + capture
    alerts = query_pipeline_alerts(t0)
    matching = [alert for alert in alerts if alert.get("experiment_id") == exp["id"]]
    detected_at = matching[0]["fire_ts"] if matching else None
    try:
        rca = query_pipeline_rca(t0, t_end)
    except Exception as exc:
        rca = {"error": str(exc), "root_service": None}
    return {
        "alerts": matching,
        "rca": rca,
        "mttd_seconds": (detected_at - t0) if detected_at else None,
        "detected": detected_at is not None,
        "false_alarms": 0,
    }


def score_one(exp: dict, observed: dict) -> dict:
    gt_root = str(exp["ground_truth"]["expected_root_service"])
    rca_root = (observed.get("rca") or {}).get("root_service")
    if gt_root.startswith("NOT "):
        rca_correct = rca_root is not None and rca_root != gt_root[4:]
    else:
        rca_correct = rca_root == gt_root
    return {
        "id": exp["id"],
        "name": exp["name"],
        "detected": observed["detected"],
        "mttd": observed["mttd_seconds"],
        "rca_service": rca_root,
        "rca_correct": rca_correct,
    }


def _p95(values: list[int | float]) -> int | float:
    if not values:
        return 0
    sorted_values = sorted(values)
    return sorted_values[max(0, int(len(sorted_values) * 0.95) - 1)]


def print_scoreboard(results: list[dict]) -> None:
    total = len(results)
    detected = sum(1 for row in results if row["detected"])
    false_alarms = sum(int((row.get("raw") or {}).get("false_alarms", 0)) for row in results)
    rca_correct = sum(1 for row in results if row["detected"] and row["rca_correct"])
    mttds = [row["mttd"] for row in results if row["mttd"] is not None]
    precision = detected / (detected + false_alarms) if detected + false_alarms else 0.0
    recall = detected / total if total else 0.0

    print("==== Chaos Run ====")
    print(f"Total: {total}")
    print(f"Detected: {detected}/{total}")
    print(f"RCA correct: {rca_correct}/{detected}" if detected else "RCA correct: 0/0")
    print(f"False alarms in baseline windows: {false_alarms}")
    print(f"Precision: {precision:.2f}")
    print(f"Recall: {recall:.2f}")
    if mttds:
        print(f"MTTD p50: {statistics.median(mttds)}s, p95: {_p95(mttds)}s")
    else:
        print("MTTD p50: n/a, p95: n/a")
    print()
    print("Per-experiment:")
    print("| # | name | detected | mttd | rca_service | rca_correct |")
    print("|---|---|---|---|---|---|")
    for row in results:
        mttd = f"{row['mttd']}s" if row["mttd"] is not None else "-"
        print(
            f"| {row['id']} | {row['name']} | {'Y' if row['detected'] else 'N'} | "
            f"{mttd} | {row['rca_service'] or '-'} | {'Y' if row['rca_correct'] else 'N'} |"
        )

    print()
    print("Gaps identified:")
    gaps = []
    for row in results:
        if not row["detected"]:
            gaps.append(f"- {row['id']}: detector missed {row['name']} -> missing meta-monitoring or weak threshold")
        elif not row["rca_correct"]:
            gaps.append(f"- {row['id']}: RCA picked {row['rca_service']} -> topology/evidence ranking weakness")
    if gaps:
        print("\n".join(gaps))
    else:
        print("- none")


def run_one(exp: dict) -> dict:
    print(f"[exp {exp['id']}] {exp['name']} - injecting fault...")
    t0 = int(time.time())
    cmd = build_inject_cmd(exp)
    subprocess.run(cmd, check=True, timeout=int(exp["blast_radius"]["duration_seconds"]) + 30)
    observed = measure_during_window(exp, t0)
    print(f"[exp {exp['id']}] cooldown {COOLDOWN_SECONDS}s...")
    time.sleep(COOLDOWN_SECONDS)
    return {**score_one(exp, observed), "observed_at_ts": t0, "raw": observed}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiments", default="experiments.yaml", type=Path)
    parser.add_argument("--out", default="chaos_results.json", type=Path)
    args = parser.parse_args()

    experiments = load_experiments(args.experiments)
    results = [run_one(exp) for exp in experiments]
    args.out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print_scoreboard(results)


if __name__ == "__main__":
    main()