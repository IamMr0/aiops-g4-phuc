import re
from collections import Counter, defaultdict

# Root cause class patterns detected from log signatures
ROOT_CAUSE_PATTERNS = {
    "memory_leak": [
        "OutOfMemoryError",
        "heap space",
        "GC pause",
        "full GC",
        "memory pressure",
    ],
    "connection_pool_exhaustion": [
        "ConnectionPool: timeout acquiring connection",
        "pool exhausted",
        "pool utilization",
    ],
    "lock_contention": [
        "deadlock detected",
        "lock timeout exceeded",
        "lock_wait_ms",
    ],
    "tls_expiry": [
        "TLS handshake failed",
        "certificate has expired",
        "x509: certificate signed by unknown authority",
    ],
    "retry_storm": [
        "Retry exhausted",
        "fallback failed, retrying",
    ],
    "rebalance_storm": [
        "consumer rebalance triggered",
        "partition reassignment",
    ],
    "slow_query": [
        "query took longer than threshold",
        "DB query latency",
    ],
    "eviction": [
        "Pod evicted",
        "OOM kill",
        "cgroup OOM",
    ],
    "rate_limit": [
        "rate limit exceeded",
        "429 returned",
    ],
    "model_drift": [
        "feature distribution drift",
        "model inference confidence dropped",
    ],
    "dns_issue": [
        "DNS resolution failed",
        "NXDOMAIN",
        "dns lookup timeout",
    ],
    "network_partition": [
        "connection refused",
        "no route to host",
        "network unreachable",
    ],
}


def clean_log_msg(msg: str) -> str:
    msg = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '<IP>', msg)
    msg = re.sub(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b', '<UUID>', msg)
    msg = re.sub(r'\b\d+\b', '<NUM>', msg)
    return msg


def detect_root_cause_classes(log_messages: list[str]) -> dict[str, float]:
    """Detect root cause class from raw log messages. Returns class -> confidence."""
    class_scores = {}
    all_text = " ".join(log_messages).lower()

    for rc_class, patterns in ROOT_CAUSE_PATTERNS.items():
        score = 0.0
        for pattern in patterns:
            count = all_text.count(pattern.lower())
            if count > 0:
                score += min(count / 10.0, 1.0)  # Cap contribution per pattern
        if score > 0:
            class_scores[rc_class] = min(score, 1.0)

    return class_scores


def get_root_cause_service(incident: dict) -> str:
    tiers = {n["id"]: n.get("tier", "api") for n in incident.get("topology", {}).get("nodes", [])}

    # 1. Traces
    anomalous_edges = []
    if "traces" in incident:
        edge_stats = defaultdict(lambda: {"count": 0, "err": 0, "p99s": []})
        for t in incident["traces"]:
            edge = (t["from"], t["to"])
            edge_stats[edge]["count"] += max(1, t.get("count", 1))
            edge_stats[edge]["err"] += t.get("error_count", 0)
            edge_stats[edge]["p99s"].append(max(1, t.get("p99_ms", 1)))

        edge_scores = []
        for edge, stats in edge_stats.items():
            err_rate = stats["err"] / stats["count"]
            p99_dev = max(stats["p99s"]) / min(stats["p99s"])
            if err_rate > 0.01 or p99_dev > 1.5:
                score = (err_rate * 20) + p99_dev
                edge_scores.append((score, edge))

        if edge_scores:
            edge_scores.sort(key=lambda x: x[0], reverse=True)
            top_edge = edge_scores[0][1]
            anomalous_edges.append({"from": top_edge[0], "to": top_edge[1]})

    if anomalous_edges:
        culprits = Counter()
        for edge in anomalous_edges:
            u, v = edge["from"], edge["to"]
            if tiers.get(v) == "store":
                culprits[u] += 1
                culprits[v] -= 1
            else:
                culprits[v] += 1
                culprits[u] -= 1
        if culprits:
            return culprits.most_common(1)[0][0]

    # 2. Logs
    if "logs" in incident:
        err_counts = Counter()
        for l in incident["logs"]:
            if l.get("level") in ("ERROR", "FATAL", "CRITICAL", "WARN", "WARNING"):
                err_counts[l["svc"]] += 1
        if err_counts:
            return err_counts.most_common(1)[0][0]

    # 3. Trigger Alert
    return incident.get("trigger_alert", {}).get("service", "unknown")


def extract_features(incident: dict) -> dict:
    affected_services = set()

    if "trigger_alert" in incident and "service" in incident["trigger_alert"]:
        affected_services.add(incident["trigger_alert"]["service"])

    # Extract ALL anomalous trace edges (not just top 1)
    trace_signatures = []
    if "traces" in incident:
        edge_stats = defaultdict(lambda: {"count": 0, "err": 0, "p99s": []})
        for t in incident["traces"]:
            edge = (t["from"], t["to"])
            edge_stats[edge]["count"] += max(1, t.get("count", 1))
            edge_stats[edge]["err"] += t.get("error_count", 0)
            edge_stats[edge]["p99s"].append(max(1, t.get("p99_ms", 1)))

        edge_scores = []
        for edge, stats in edge_stats.items():
            err_rate = stats["err"] / stats["count"]
            p99_dev = max(stats["p99s"]) / min(stats["p99s"])
            if err_rate > 0.01 or p99_dev > 1.5:
                score = (err_rate * 20) + p99_dev
                edge_scores.append((score, edge, err_rate, p99_dev))

        if edge_scores:
            edge_scores.sort(key=lambda x: x[0], reverse=True)
            # Include top 3 anomalous edges instead of just 1
            for score, edge, err_rate, p99_dev in edge_scores[:3]:
                trace_signatures.append({
                    "from": edge[0],
                    "to": edge[1],
                    "p99_deviation_ratio": round(p99_dev, 2),
                    "error_rate": round(err_rate, 2)
                })
                affected_services.add(edge[0])
                affected_services.add(edge[1])

    root_cause = get_root_cause_service(incident)

    # Extract log signatures — more comprehensive
    log_signatures = []
    raw_log_messages = []
    root_cause_raw_logs = []
    if "logs" in incident:
        cleaned_logs = []
        for l in incident["logs"]:
            raw_log_messages.append(l["msg"])
            if l.get("svc") == root_cause:
                root_cause_raw_logs.append(l["msg"])
            if l.get("level") in ("ERROR", "WARN", "WARNING", "FATAL", "CRITICAL"):
                affected_services.add(l["svc"])
                cleaned_logs.append(clean_log_msg(l["msg"]))

        counter = Counter(cleaned_logs)
        for msg, count in counter.most_common(20):  # Increase from 10 to 20
            log_signatures.append(msg)

    # Detect root cause classes from raw logs
    root_cause_classes = {}
    if root_cause_raw_logs:
        root_cause_classes = detect_root_cause_classes(root_cause_raw_logs)
    if not root_cause_classes:
        root_cause_classes = detect_root_cause_classes(raw_log_messages)

    return {
        "affected_services": list(affected_services),
        "log_signatures": log_signatures,
        "trace_signatures": trace_signatures,
        "root_cause_service": root_cause,
        "root_cause_classes": root_cause_classes,
    }
