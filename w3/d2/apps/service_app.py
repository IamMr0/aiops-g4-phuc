from __future__ import annotations

import os
import random
import time
from typing import Any

import requests
from fastapi import FastAPI, HTTPException


SERVICE_NAME = os.getenv("SERVICE_NAME", "service")
DEPENDENCIES = {
    "api-gateway": ["dns-resolver", "auth-svc", "checkout-svc"],
    "checkout-svc": ["payment-svc", "inventory-svc"],
    "payment-svc": ["payment-db"],
    "inventory-svc": ["inventory-db"],
}

app = FastAPI(title=f"W3-D2 lightweight service: {SERVICE_NAME}")
fault_state: dict[str, Any] = {}


def _active_fault() -> dict[str, Any] | None:
    if not fault_state:
        return None
    if time.time() >= float(fault_state.get("ends_at", 0)):
        fault_state.clear()
        return None
    return fault_state


def _apply_local_fault() -> None:
    fault = _active_fault()
    if not fault:
        return

    fault_type = str(fault.get("type", ""))
    if fault_type in {"latency", "dns_latency"}:
        time.sleep(float(fault.get("latency_ms", 500)) / 1000.0)
    elif fault_type == "cpu_saturation":
        time.sleep(0.25)
    elif fault_type == "network_loss":
        if random.random() < float(fault.get("loss_percent", 30)) / 100.0:
            raise HTTPException(status_code=503, detail="simulated packet loss")
    elif fault_type in {"availability", "network_partition"}:
        raise HTTPException(status_code=503, detail=f"simulated {fault_type}")
    elif fault_type == "http_error":
        if random.random() < float(fault.get("error_percent", 20)) / 100.0:
            raise HTTPException(status_code=500, detail="simulated http error")
    elif fault_type in {"memory", "disk_fill", "time_skew"}:
        raise HTTPException(status_code=503, detail=f"simulated {fault_type}")


def _call_dependency(service: str) -> dict[str, Any]:
    url = f"http://{service}:8080/health"
    started = time.perf_counter()
    try:
        response = requests.get(url, timeout=2.0)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "service": service,
            "status_code": response.status_code,
            "ok": response.status_code == 200,
            "latency_ms": latency_ms,
        }
    except requests.RequestException as exc:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "service": service,
            "status_code": 0,
            "ok": False,
            "latency_ms": latency_ms,
            "error": exc.__class__.__name__,
        }


@app.get("/health")
def health() -> dict[str, Any]:
    _apply_local_fault()
    dependency_results = [_call_dependency(dep) for dep in DEPENDENCIES.get(SERVICE_NAME, [])]
    if any(not item["ok"] for item in dependency_results):
        raise HTTPException(
            status_code=503,
            detail={"service": SERVICE_NAME, "dependencies": dependency_results},
        )
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "fault": _active_fault() or None,
        "dependencies": dependency_results,
    }


@app.get("/checkout/health")
def checkout_health() -> dict[str, Any]:
    if SERVICE_NAME != "api-gateway":
        return health()
    return health()


@app.post("/fault")
def inject_fault(payload: dict[str, Any]) -> dict[str, Any]:
    duration = int(payload.get("duration_seconds", 10))
    fault_type = str(payload.get("type", "latency"))
    fault_state.clear()
    fault_state.update(payload)
    fault_state["type"] = fault_type
    fault_state["started_at"] = int(time.time())
    fault_state["ends_at"] = time.time() + duration
    return {"service": SERVICE_NAME, "fault": fault_state}


@app.post("/clear_fault")
def clear_fault() -> dict[str, str]:
    fault_state.clear()
    return {"service": SERVICE_NAME, "status": "cleared"}


@app.get("/metrics")
def metrics() -> dict[str, Any]:
    fault = _active_fault()
    fault_type = str((fault or {}).get("type", ""))
    return {
        "service": SERVICE_NAME,
        "healthy": fault_type not in {"availability", "network_partition", "memory", "disk_fill"},
        "fault_type": fault_type or None,
        "latency_p99_ms": 650 if fault_type in {"latency", "dns_latency", "cpu_saturation"} else 80,
        "error_rate": 0.35 if fault_type in {"network_loss", "http_error", "availability"} else 0.0,
        "cpu_used_ratio": 0.90 if fault_type == "cpu_saturation" else 0.15,
        "memory_used_ratio": 0.95 if fault_type == "memory" else 0.35,
        "disk_used_ratio": 0.95 if fault_type == "disk_fill" else 0.45,
    }