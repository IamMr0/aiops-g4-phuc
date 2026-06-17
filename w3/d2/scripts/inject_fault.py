#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time
from typing import Any

import requests


SERVICE_PORTS = {
    "frontend": 8100,
    "api-gateway": 8080,
    "payment-svc": 8101,
    "inventory-svc": 8102,
    "checkout-svc": 8103,
    "auth-svc": 8104,
    "log-collector": 8105,
    "dns-resolver": 8106,
    "cache-svc": 8107,
    "payment-db": 8108,
    "inventory-db": 8109,
    "notification-svc": 8110,
}


FAULT_DEFAULTS: dict[str, dict[str, Any]] = {
    "latency": {"latency_ms": 500},
    "network_loss": {"loss_percent": 30},
    "availability": {},
    "cpu_saturation": {},
    "memory": {},
    "disk_fill": {},
    "time_skew": {"skew_seconds": 60},
    "network_partition": {},
    "dns_latency": {"latency_ms": 2000},
    "http_error": {"error_percent": 20},
}


def _service_url(service: str) -> str:
    if service not in SERVICE_PORTS:
        raise SystemExit(f"unknown service target: {service}")
    return f"http://localhost:{SERVICE_PORTS[service]}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--fault-type", required=True)
    parser.add_argument("--duration", type=int, required=True)
    parser.add_argument("--expected-root", required=True)
    args = parser.parse_args()

    payload = {
        "id": int(args.id),
        "name": args.name,
        "target": args.service,
        "fault_type": args.fault_type,
        "type": args.fault_type,
        "duration_seconds": args.duration,
        "expected_root_service": args.expected_root,
        "start_ts": int(time.time()),
    }
    payload.update(FAULT_DEFAULTS.get(args.fault_type, {}))

    target_url = _service_url(args.service)
    requests.post(f"{target_url}/fault", json=payload, timeout=10).raise_for_status()
    requests.post("http://localhost:8000/events", json=payload, timeout=10).raise_for_status()

    try:
        time.sleep(args.duration)
    finally:
        try:
            requests.post(f"{target_url}/clear_fault", timeout=10)
        except requests.RequestException:
            pass


if __name__ == "__main__":
    main()