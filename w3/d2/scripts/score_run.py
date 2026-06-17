#!/usr/bin/env python3
"""Read chaos_results.json + probe.log and emit the required scoreboard."""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def parse_probe(path: Path) -> dict:
    if not path.exists():
        return {"total": 0, "pass": 0, "pass_rate": None}
    total = 0
    passed = 0
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            total += 1
            if parts[1] == "pass":
                passed += 1
    return {
        "total": total,
        "pass": passed,
        "pass_rate": (passed / total) if total else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default="chaos_results.json", type=Path)
    parser.add_argument("--probe", default="probe.log", type=Path)
    args = parser.parse_args()

    results = json.loads(args.results.read_text(encoding="utf-8"))
    probe = parse_probe(args.probe)

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
        p50 = statistics.median(mttds)
        p95 = sorted(mttds)[max(0, int(len(mttds) * 0.95) - 1)]
        print(f"MTTD p50: {p50}s, p95: {p95}s")
    else:
        print("MTTD p50: n/a, p95: n/a")
    if probe["pass_rate"] is not None:
        print(f"External probe pass-rate: {probe['pass_rate']:.2%}")
    else:
        print("External probe pass-rate: n/a")

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
    print("\n".join(gaps) if gaps else "- none")


if __name__ == "__main__":
    main()