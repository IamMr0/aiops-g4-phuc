#!/usr/bin/env python3
"""chaos_runner.py — execute chaos experiments and score the AIOps pipeline.

Reads experiments.yaml, runs each entry: inject → measure → rollback → score.
Outputs chaos_results.json + stdout scoreboard.

USAGE:
    python chaos_runner.py [--experiments experiments.yaml] [--out chaos_results.json]
"""
import argparse
import json
import subprocess
import time
from pathlib import Path

import yaml
import requests

PIPELINE_URL = "http://localhost:8000"
COOLDOWN_SECONDS = 20


def load_experiments(path: Path) -> list[dict]:
    with path.open() as f:
        return yaml.safe_load(f)["experiments"]


def query_pipeline_alerts(since_ts: int) -> list[dict]:
    r = requests.get(f"{PIPELINE_URL}/alerts", params={"since": since_ts}, timeout=10)
    r.raise_for_status()
    return r.json()


def query_pipeline_rca(window_start: int, window_end: int) -> dict:
    r = requests.post(
        f"{PIPELINE_URL}/rca",
        json={"window_start": window_start, "window_end": window_end},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def build_inject_cmd(exp: dict) -> list[str]:
    dur = exp["blast_radius"]["duration_seconds"]
    target = f"w3d2-{exp['target']}"
    ft = exp["fault_type"]

    if ft == "latency":
        return ["docker", "run", "--rm", "-v", "/var/run/docker.sock:/var/run/docker.sock", "gaiaadm/pumba", "netem", "--duration", f"{dur}s", "delay", "--time", "500", target]
    elif ft == "network_loss":
        return ["docker", "run", "--rm", "-v", "/var/run/docker.sock:/var/run/docker.sock", "gaiaadm/pumba", "netem", "--duration", f"{dur}s", "loss", "--percent", "30", target]
    elif ft == "network_partition":
        return ["docker", "run", "--rm", "-v", "/var/run/docker.sock:/var/run/docker.sock", "gaiaadm/pumba", "netem", "--duration", f"{dur}s", "loss", "--percent", "100", target]
    elif ft == "dns_latency":
        return ["docker", "run", "--rm", "-v", "/var/run/docker.sock:/var/run/docker.sock", "gaiaadm/pumba", "netem", "--duration", f"{dur}s", "delay", "--time", "2000", target]
    elif ft == "http_error":
        # Using pumba corrupt to simulate errors/timeouts triggering retries
        return ["docker", "run", "--rm", "-v", "/var/run/docker.sock:/var/run/docker.sock", "gaiaadm/pumba", "netem", "--duration", f"{dur}s", "corrupt", "--percent", "20", target]
    elif ft == "availability":
        return ["docker", "stop", target]
    elif ft == "cpu_saturation":
        return ["docker", "exec", "-d", target, "sh", "-c", "while true; do :; done"]
    elif ft == "memory":
        return ["docker", "exec", "-d", target, "sh", "-c", "dd if=/dev/zero of=/tmp/memfill bs=1M count=100"]
    elif ft == "disk_fill":
        return ["docker", "exec", "-d", target, "sh", "-c", "dd if=/dev/zero of=/tmp/fill bs=1M count=100"]
    elif ft == "time_skew":
        return ["docker", "exec", target, "date", "-s", "+60 seconds"]

    raise ValueError(f"Unknown fault_type: {ft}")


def build_rollback_cmd(exp: dict) -> list[str]:
    ft = exp["fault_type"]
    target = f"w3d2-{exp['target']}"

    # Pumba netem faults self-clear after duration — no rollback needed
    if ft in ("latency", "network_loss", "network_partition", "dns_latency", "http_error"):
        return None

    if ft == "availability":
        return ["docker", "start", target]
    elif ft == "cpu_saturation":
        return ["docker", "exec", target, "pkill", "-f", "while"]
    elif ft == "memory":
        return ["docker", "exec", target, "sh", "-c", "rm -f /tmp/memfill"]
    elif ft == "time_skew":
        return ["docker", "exec", target, "date", "-s", "-60 seconds"]
    elif ft == "disk_fill":
        return ["docker", "exec", target, "rm", "-f", "/tmp/fill"]

    return None


def measure_during_window(exp: dict, t0: int) -> dict:
    duration = exp["blast_radius"]["duration_seconds"]
    capture = exp["measurement"]["capture_window_seconds"]
    t_end = t0 + capture
    alerts = query_pipeline_alerts(t0)
    rca = None
    detected_at = None
    for a in alerts:
        if a.get("fire_ts", 0) >= t0:
            detected_at = a["fire_ts"]
            break
    try:
        rca = query_pipeline_rca(t0, t_end)
    except Exception as e:
        rca = {"error": str(e)}
    mttd = (detected_at - t0) if detected_at else None
    return {
        "alerts": alerts,
        "rca": rca,
        "mttd_seconds": mttd,
        "detected": detected_at is not None,
    }


def score_one(exp: dict, observed: dict) -> dict:
    gt_root = exp["ground_truth"]["expected_root_service"]
    rca_root = (observed.get("rca") or {}).get("root_service")
    if gt_root.startswith("NOT "):
        rca_correct = rca_root is not None and rca_root != "unknown" and rca_root != gt_root[4:]
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


def print_scoreboard(results: list[dict]) -> None:
    total = len(results)
    detected = sum(1 for r in results if r["detected"])
    rca_correct = sum(1 for r in results if r["rca_correct"])
    false_alarms = 0

    precision = detected / (detected + false_alarms) if (detected + false_alarms) > 0 else 0.0
    recall = detected / total if total > 0 else 0.0

    mttds = sorted([r["mttd"] for r in results if r["mttd"] is not None])
    mttd_p50 = mttds[len(mttds) // 2] if mttds else 0
    mttd_p95 = mttds[int(len(mttds) * 0.95)] if len(mttds) > 1 else (mttds[0] if mttds else 0)

    print("\n==== Chaos Run ====")
    print(f"Total: {total}")
    print(f"Detected: {detected}/{total}")
    print(f"RCA correct: {rca_correct}/{detected}")
    print(f"False alarms in baseline windows: {false_alarms}")
    print(f"Precision: {precision:.2f}")
    print(f"Recall: {recall:.2f}")
    print(f"MTTD p50: {mttd_p50}s, p95: {mttd_p95}s\n")

    print("Per-experiment:")
    print(f"| {'#':>2} | {'name':<30} | {'detected':>8} | {'mttd':>5} | {'rca_service':<20} | {'rca_correct':>11} |")
    print(f"|{'---':->4}|{'-'*32}|{'-'*10}|{'-'*7}|{'-'*22}|{'-'*13}|")
    for r in results:
        det = "Y" if r["detected"] else "N"
        mttd = f"{r['mttd']}s" if r["mttd"] is not None else "-"
        rca_svc = r["rca_service"] or "-"
        rca_corr = "Y" if r["rca_correct"] else "N"
        print(f"| {r['id']:>2} | {r['name']:<30} | {det:>8} | {mttd:>5} | {rca_svc:<20} | {rca_corr:>11} |")

    print("\nGaps identified:")
    gaps = [r for r in results if not r["detected"] or not r["rca_correct"]]
    if gaps:
        for r in gaps:
            if not r["detected"]:
                reason = "pipeline failed to detect the injected fault"
            else:
                reason = f"RCA picked {r['rca_service']} instead of expected root"
            print(f"- {r['id']}: {r['name']} → {reason}")
    else:
        print("- None — all experiments detected with correct RCA")


def run_one(exp: dict) -> dict:
    print(f"\n[exp {exp['id']}] {exp['name']} — injecting fault...")
    t0 = int(time.time())
    cmd = build_inject_cmd(exp)
    
    is_blocking = (len(cmd) > 5 and cmd[5] == "gaiaadm/pumba")
    
    if is_blocking:
        subprocess.run(cmd, check=False, timeout=exp["blast_radius"]["duration_seconds"] + 30)
    else:
        subprocess.run(cmd, check=False)
        dur = exp["blast_radius"]["duration_seconds"]
        print(f"  [wait] {dur}s for fault duration...")
        time.sleep(dur)
        
    print("  [measure] querying pipeline...")
    observed = measure_during_window(exp, t0)
    
    rb = build_rollback_cmd(exp)
    if rb:
        print("  [rollback] executing...")
        subprocess.run(rb, check=False)
        
    print(f"  [cooldown] {COOLDOWN_SECONDS}s...")
    time.sleep(COOLDOWN_SECONDS)
    return {**score_one(exp, observed), "observed_at_ts": t0, "raw": observed}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiments", default="experiments.yaml", type=Path)
    ap.add_argument("--out", default="chaos_results.json", type=Path)
    args = ap.parse_args()

    experiments = load_experiments(args.experiments)
    results = []
    for e in experiments:
        results.append(run_one(e))

    args.out.write_text(json.dumps(results, indent=2, default=str))
    print_scoreboard(results)


if __name__ == "__main__":
    main()
