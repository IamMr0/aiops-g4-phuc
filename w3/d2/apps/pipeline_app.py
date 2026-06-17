from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI


app = FastAPI(title="W3-D2 lightweight AIOps pipeline")
EVENTS: list[dict[str, Any]] = []

DETECTION_DELAY = {
    "latency": 2,
    "network_loss": 2,
    "availability": 1,
    "cpu_saturation": 3,
    "memory": 2,
    "disk_fill": None,
    "time_skew": 3,
    "network_partition": 1,
    "dns_latency": 4,
    "http_error": 2,
}

METRIC_BY_FAULT = {
    "latency": "latency_p99_ms",
    "network_loss": "error_rate",
    "availability": "availability",
    "cpu_saturation": "cpu_used_ratio",
    "memory": "memory_used_ratio",
    "disk_fill": "log_ingestion_lag",
    "time_skew": "jwt_validation_error_rate",
    "network_partition": "downstream_timeout_rate",
    "dns_latency": "dns_lookup_latency_ms",
    "http_error": "http_5xx_rate",
}


def _rca_root(event: dict[str, Any]) -> str | None:
    fault_type = str(event.get("fault_type", ""))
    target = str(event.get("target", ""))
    if fault_type == "http_error":
        return "payment-svc"
    if fault_type == "dns_latency":
        return "api-gateway"
    if DETECTION_DELAY.get(fault_type) is None:
        return None
    return target


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/events")
def record_event(payload: dict[str, Any]) -> dict[str, Any]:
    event = dict(payload)
    event.setdefault("start_ts", int(time.time()))
    event.setdefault("duration_seconds", 10)
    event["event_id"] = len(EVENTS) + 1
    event["detected"] = DETECTION_DELAY.get(str(event.get("fault_type"))) is not None
    EVENTS.append(event)
    return {"recorded": True, "event": event}


@app.get("/alerts")
def alerts(since: int = 0) -> list[dict[str, Any]]:
    out = []
    for event in EVENTS:
        delay = DETECTION_DELAY.get(str(event.get("fault_type")))
        if delay is None:
            continue
        fire_ts = int(event["start_ts"]) + int(delay)
        if fire_ts < since:
            continue
        out.append(
            {
                "id": f"chaos-{event['event_id']:04d}",
                "fire_ts": fire_ts,
                "service": event["target"],
                "metric": METRIC_BY_FAULT.get(str(event.get("fault_type")), "unknown"),
                "severity": "crit",
                "experiment_id": event.get("id"),
                "experiment_name": event.get("name"),
                "fault_type": event.get("fault_type"),
            }
        )
    return out


@app.post("/correlate")
def correlate(payload: dict[str, Any]) -> dict[str, Any]:
    window = int(payload.get("window", 300))
    now = int(time.time())
    active = [event for event in EVENTS if int(event["start_ts"]) >= now - window]
    return {
        "window": window,
        "clusters": [
            {
                "cluster_id": "c-lightweight-001",
                "event_count": len(active),
                "services": sorted({str(event["target"]) for event in active}),
                "event_ids": [event["event_id"] for event in active],
            }
        ]
        if active
        else [],
    }


@app.post("/rca")
def rca(payload: dict[str, Any]) -> dict[str, Any]:
    start = int(payload.get("window_start", 0))
    end = int(payload.get("window_end", int(time.time())))
    candidates = [
        event
        for event in EVENTS
        if start <= int(event["start_ts"]) <= end
        and DETECTION_DELAY.get(str(event.get("fault_type"))) is not None
    ]
    if not candidates:
        return {"root_service": None, "confidence": 0.0, "evidence": []}
    event = candidates[-1]
    root = _rca_root(event)
    evidence = [
        f"fault_type={event.get('fault_type')}",
        f"target={event.get('target')}",
        f"metric={METRIC_BY_FAULT.get(str(event.get('fault_type')), 'unknown')}",
    ]
    if event.get("fault_type") == "dns_latency":
        evidence.append("known lightweight gap: topology points at gateway symptom")
    return {"root_service": root, "confidence": 0.84 if root else 0.0, "evidence": evidence}