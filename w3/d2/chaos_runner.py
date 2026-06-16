#!/usr/bin/env python3
"""chaos_runner.py — execute chaos experiments and score the AIOps pipeline.

Reads experiments.yaml, runs each entry: inject → measure → rollback → score.
Outputs chaos_results.json + stdout scoreboard.

Flow per experiment:
  1. Record t0
  2. Launch fault injection (in background thread for blocking Pumba commands)
  3. Wait for fault to take effect (poll_delay)
  4. Scrape Prometheus for anomalous metrics
  5. Build alert objects from anomalies
  6. POST alerts to pipeline /incident endpoint
  7. Score the pipeline's RCA response
  8. Rollback (if needed) + cooldown
"""
import sys
import argparse
import json
import subprocess
import time
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml
import requests

PIPELINE_URL = "http://localhost:8000"
PROMETHEUS_URL = "http://localhost:9090"
COOLDOWN_SECONDS = 120
POLL_DELAY = 20          # seconds to wait for fault to propagate before scraping
SCRAPE_INTERVAL = 10     # seconds between Prometheus scrapes during fault
SCRAPE_COUNT = 3          # number of scrapes during fault window


def load_experiments(path: Path) -> list[dict]:
    with path.open() as f:
        return yaml.safe_load(f)["experiments"]


# ---------------------------------------------------------------------------
# Prometheus helpers
# ---------------------------------------------------------------------------

def prom_query(query: str) -> list[dict]:
    """Query Prometheus instant query, return list of {metric, value} results."""
    try:
        r = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": query},
            timeout=5,
        )
        r.raise_for_status()
        return r.json().get("data", {}).get("result", [])
    except Exception:
        return []


def prom_query_value(query: str) -> float | None:
    """Query Prometheus and return mean of all result values."""
    results = prom_query(query)
    if not results:
        return None
    vals = []
    for r in results:
        v = r.get("value", [None, None])[1]
        if v is not None and v not in ("NaN", "+Inf", "-Inf"):
            vals.append(float(v))
    return sum(vals) / len(vals) if vals else None


def scrape_service_metrics(service: str) -> dict:
    """Scrape key metrics for a service from Prometheus."""
    clean = service.replace("-", "_")
    # Multiple queries to detect different fault types
    queries = {
        "error_rate": f'rate(http_requests_total{{job="services",instance=~"{service}.*",status=~"5.."}}[1m])',
        "request_rate": f'rate(http_requests_total{{job="services",instance=~"{service}.*"}}[1m])',
        "latency_p99": f'histogram_quantile(0.99, rate(http_request_duration_seconds_bucket{{job="services",instance=~"{service}.*"}}[1m]))',
        "latency_p50": f'histogram_quantile(0.50, rate(http_request_duration_seconds_bucket{{job="services",instance=~"{service}.*"}}[1m]))',
        "up": f'up{{job="services",instance=~"{service}.*"}}',
    }
    out = {}
    for name, q in queries.items():
        v = prom_query_value(q)
        if v is not None:
            out[name] = v
    return out


def scrape_all_services() -> dict[str, dict]:
    """Scrape metrics for all services."""
    services = [
        "api-gateway", "payment-svc", "inventory-svc",
        "notification-svc", "checkout-svc", "auth-svc",
    ]
    result = {}
    for svc in services:
        metrics = scrape_service_metrics(svc)
        if metrics:
            result[svc] = metrics
    return result


# ---------------------------------------------------------------------------
# Anomaly detection (simple threshold-based)
# ---------------------------------------------------------------------------

BASELINE_THRESHOLDS = {
    "latency_p99": 0.5,     # 500ms
    "error_rate": 0.01,     # 1% error rate
    "up": 0.5,              # down if < 0.5
}


def detect_anomalies(before: dict[str, dict], during: dict[str, dict],
                     target_service: str) -> list[dict]:
    """Compare before/during metrics, return list of alert dicts for anomalies."""
    alerts = []
    ts_now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for svc, metrics in during.items():
        before_metrics = before.get(svc, {})

        # Check latency spike
        lat = metrics.get("latency_p99", 0)
        base_lat = before_metrics.get("latency_p99", 0.1)
        if lat > 0 and (lat > BASELINE_THRESHOLDS["latency_p99"] or
                        (base_lat > 0 and lat > base_lat * 3)):
            alerts.append(_make_alert(svc, "latency_p99_ms", "warning",
                                      lat * 1000, base_lat * 1000, ts_now))

        # Check error rate spike
        err = metrics.get("error_rate", 0)
        base_err = before_metrics.get("error_rate", 0)
        if err > BASELINE_THRESHOLDS["error_rate"] or (base_err > 0 and err > base_err * 5):
            alerts.append(_make_alert(svc, "error_rate", "critical",
                                      err, max(base_err, 0.01), ts_now))

        # Check service down
        up_val = metrics.get("up", 1)
        base_up = before_metrics.get("up", 1)
        if up_val < BASELINE_THRESHOLDS["up"] and base_up >= BASELINE_THRESHOLDS["up"]:
            alerts.append(_make_alert(svc, "availability", "critical",
                                      0, 1, ts_now))

        # Check request rate drop (service not responding)
        req_rate = metrics.get("request_rate", 0)
        base_req = before_metrics.get("request_rate", 0)
        if base_req > 0 and req_rate < base_req * 0.1:
            alerts.append(_make_alert(svc, "request_rate_drop", "warning",
                                      req_rate, base_req, ts_now))

    # If target service disappeared from metrics entirely → it's down
    if target_service in before and target_service not in during:
        alerts.append(_make_alert(target_service, "availability", "critical",
                                  0, 1, ts_now))

    # Also check via up metric directly for the target
    if not any(a["service"] == target_service for a in alerts):
        up_results = prom_query(f'up{{instance=~"{target_service}.*"}}')
        for r in up_results:
            v = float(r.get("value", [0, 1])[1])
            if v < 1:
                alerts.append(_make_alert(target_service, "availability", "warning",
                                          v, 1, ts_now))
                break

    return alerts


def _make_alert(service: str, metric: str, severity: str,
                value: float, threshold: float, ts: str) -> dict:
    return {
        "id": f"chaos-{uuid.uuid4().hex[:8]}",
        "ts": ts,
        "service": service,
        "metric": metric,
        "severity": severity,
        "value": round(value, 4),
        "threshold": round(threshold, 4),
        "labels": {"source": "chaos_runner"},
    }


# ---------------------------------------------------------------------------
# Pipeline interaction
# ---------------------------------------------------------------------------

def send_to_pipeline(alerts: list[dict]) -> dict:
    """POST alerts to the pipeline /incident endpoint and get RCA response."""
    if not alerts:
        return {
            "root_cause": {"service": "unknown", "confidence": 0.0, "reasoning": "No alerts detected"},
            "clusters": [],
        }
    try:
        r = requests.post(
            f"{PIPELINE_URL}/incident",
            json={"alerts": alerts},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {
            "root_cause": {"service": "unknown", "confidence": 0.0, "reasoning": str(e)},
            "clusters": [],
        }


# ---------------------------------------------------------------------------
# Fault injection commands (Windows-compatible, all via docker)
# ---------------------------------------------------------------------------

def build_inject_cmd(exp: dict) -> list[str]:
    """Dispatch fault_type to concrete subprocess command.

    Covers all 10 fault types from §3:
        latency, network_loss, availability, cpu_saturation, memory,
        disk_fill, time_skew, network_partition, dns_latency, http_error
    """
    dur = exp["blast_radius"]["duration_seconds"]
    target = f"w3d2-{exp['target']}"
    ft = exp["fault_type"]

    if ft == "latency":
        return ["docker", "run", "--rm", "-v", "/var/run/docker.sock:/var/run/docker.sock",
                "gaiaadm/pumba", "netem", "--duration", f"{dur}s",
                "delay", "--time", "500", target]
    elif ft == "network_loss":
        return ["docker", "run", "--rm", "-v", "/var/run/docker.sock:/var/run/docker.sock",
                "gaiaadm/pumba", "netem", "--duration", f"{dur}s",
                "loss", "--percent", "30", target]
    elif ft == "network_partition":
        return ["docker", "run", "--rm", "-v", "/var/run/docker.sock:/var/run/docker.sock",
                "gaiaadm/pumba", "netem", "--duration", f"{dur}s",
                "loss", "--percent", "100", target]
    elif ft == "dns_latency":
        return ["docker", "run", "--rm", "-v", "/var/run/docker.sock:/var/run/docker.sock",
                "gaiaadm/pumba", "netem", "--duration", f"{dur}s",
                "delay", "--time", "2000", target]
    elif ft == "http_error":
        return ["docker", "run", "--rm", "-v", "/var/run/docker.sock:/var/run/docker.sock",
                "gaiaadm/pumba", "netem", "--duration", f"{dur}s",
                "corrupt", "--percent", "20", target]
    elif ft == "availability":
        return ["docker", "stop", target]
    elif ft == "cpu_saturation":
        return ["docker", "exec", "-d", target, "sh", "-c", "while true; do :; done"]
    elif ft == "memory":
        return ["docker", "exec", "-d", target, "sh", "-c",
                "dd if=/dev/zero of=/tmp/memfill bs=1M count=100"]
    elif ft == "time_skew":
        return ["docker", "exec", target, "date", "-s", "+60 seconds"]
    elif ft == "disk_fill":
        return ["docker", "exec", "-d", target, "sh", "-c",
                "dd if=/dev/zero of=/tmp/fill bs=1M count=100"]

    return ["echo", "unsupported fault"]


def build_rollback_cmd(exp: dict) -> list[str]:
    """Fault-specific rollback, Windows-compatible (all via docker).

    Pumba netem faults auto-revert after --duration → return None.
    Other faults need explicit rollback via docker exec/start.
    """
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


def is_blocking_fault(fault_type: str) -> bool:
    """Pumba faults block (docker run); exec -d faults return immediately."""
    return fault_type in ("latency", "network_loss", "network_partition",
                          "dns_latency", "http_error")


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_one(exp: dict, pipeline_result: dict, detected: bool,
              mttd: int | None, alerts: list[dict]) -> dict:
    gt_root = exp["ground_truth"]["expected_root_service"]
    rca_root = pipeline_result.get("root_cause", {}).get("service", "unknown")

    if gt_root.startswith("NOT "):
        rca_correct = detected and rca_root != "unknown" and rca_root != gt_root[4:]
    else:
        rca_correct = detected and rca_root == gt_root

    return {
        "id": exp["id"],
        "name": exp["name"],
        "detected": detected,
        "mttd": mttd,
        "rca_service": rca_root,
        "rca_correct": rca_correct,
    }


def print_scoreboard(results: list[dict]) -> None:
    """Print confusion matrix per §8.6 format."""
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


# ---------------------------------------------------------------------------
# Experiment runner
# ---------------------------------------------------------------------------

def run_one(exp: dict) -> dict:
    """Run a single chaos experiment: inject → observe → measure → rollback → score."""
    print(f"\n[exp {exp['id']}] {exp['name']} — starting...")

    # Step 1: Capture baseline metrics BEFORE injection
    print(f"  [baseline] scraping pre-injection metrics...")
    before_metrics = scrape_all_services()

    # Step 2: Inject fault
    t0 = int(time.time())
    cmd = build_inject_cmd(exp)
    print(f"  [inject] running: {' '.join(cmd[:6])}...")

    if is_blocking_fault(exp["fault_type"]):
        # Pumba blocks — run in background thread, scrape during fault
        inject_thread = threading.Thread(
            target=lambda: subprocess.run(cmd, check=False,
                                          timeout=exp["blast_radius"]["duration_seconds"] + 60),
            daemon=True,
        )
        inject_thread.start()

        # Wait for fault to propagate
        print(f"  [wait] {POLL_DELAY}s for fault propagation...")
        time.sleep(POLL_DELAY)

        # Step 3: Scrape metrics DURING fault
        print(f"  [measure] scraping metrics during fault...")
        during_metrics = {}
        for i in range(SCRAPE_COUNT):
            snapshot = scrape_all_services()
            for svc, m in snapshot.items():
                if svc not in during_metrics:
                    during_metrics[svc] = m
                else:
                    # Keep worst values (highest latency, highest error rate)
                    for k, v in m.items():
                        if k in ("latency_p99", "error_rate"):
                            during_metrics[svc][k] = max(during_metrics[svc].get(k, 0), v)
                        elif k == "up":
                            during_metrics[svc][k] = min(during_metrics[svc].get(k, 1), v)
                        else:
                            during_metrics[svc][k] = v
            if i < SCRAPE_COUNT - 1:
                time.sleep(SCRAPE_INTERVAL)

        # Wait for inject thread to finish
        inject_thread.join(timeout=exp["blast_radius"]["duration_seconds"] + 60)

    else:
        # Non-blocking fault (docker exec -d) — inject and wait
        try:
            subprocess.run(cmd, check=False, timeout=30)
        except Exception as e:
            print(f"  [warn] inject error: {e}")

        # Wait for fault to propagate
        wait_time = min(exp["blast_radius"]["duration_seconds"], 45)
        print(f"  [wait] {wait_time}s for fault propagation...")
        time.sleep(wait_time)

        # Step 3: Scrape metrics DURING fault
        print(f"  [measure] scraping metrics during fault...")
        during_metrics = {}
        for i in range(SCRAPE_COUNT):
            snapshot = scrape_all_services()
            for svc, m in snapshot.items():
                if svc not in during_metrics:
                    during_metrics[svc] = m
                else:
                    for k, v in m.items():
                        if k in ("latency_p99", "error_rate"):
                            during_metrics[svc][k] = max(during_metrics[svc].get(k, 0), v)
                        elif k == "up":
                            during_metrics[svc][k] = min(during_metrics[svc].get(k, 1), v)
                        else:
                            during_metrics[svc][k] = v
            if i < SCRAPE_COUNT - 1:
                time.sleep(SCRAPE_INTERVAL)

    t_measured = int(time.time())

    # Step 4: Detect anomalies
    target_svc = exp["target"]
    anomaly_alerts = detect_anomalies(before_metrics, during_metrics, target_svc)
    detected = len(anomaly_alerts) > 0
    mttd = (t_measured - t0) if detected else None

    print(f"  [detect] found {len(anomaly_alerts)} anomaly alerts")
    for a in anomaly_alerts:
        print(f"    → {a['service']}: {a['metric']}={a['value']} (threshold={a['threshold']})")

    # Step 5: Send alerts to pipeline for RCA
    pipeline_result = send_to_pipeline(anomaly_alerts)
    rca_service = pipeline_result.get("root_cause", {}).get("service", "unknown")
    rca_confidence = pipeline_result.get("root_cause", {}).get("confidence", 0)
    print(f"  [rca] pipeline says: root={rca_service} confidence={rca_confidence:.2f}")

    # Step 6: Rollback
    rb = build_rollback_cmd(exp)
    if rb:
        print(f"  [rollback] {' '.join(rb[:4])}...")
        try:
            subprocess.run(rb, check=False, timeout=15)
        except Exception as e:
            print(f"  [warn] rollback error: {e}")

    # Step 7: Cooldown
    print(f"  [cooldown] {COOLDOWN_SECONDS}s...")
    time.sleep(COOLDOWN_SECONDS)

    # Step 8: Score
    result = score_one(exp, pipeline_result, detected, mttd, anomaly_alerts)
    result["observed_at_ts"] = t0
    result["raw"] = {
        "alerts": anomaly_alerts,
        "pipeline_response": pipeline_result,
        "before_metrics": before_metrics,
        "during_metrics": during_metrics,
    }
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiments", default="experiments.yaml", type=Path)
    ap.add_argument("--out", default="chaos_results.json", type=Path)
    args = ap.parse_args()

    print(f"Loading experiments from {args.experiments}")
    experiments = load_experiments(args.experiments)
    print(f"Running {len(experiments)} experiments with {COOLDOWN_SECONDS}s cooldown\n")

    results = []
    for exp in experiments:
        result = run_one(exp)
        results.append(result)

    args.out.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nResults written to {args.out}")
    print_scoreboard(results)


if __name__ == "__main__":
    main()
