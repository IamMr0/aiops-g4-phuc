"""
correlate.py — Alert correlation layer.

Groups a batch of alerts into incident clusters using:
  1. Temporal session windowing  (gap_sec)
  2. Service-graph proximity via union-find + shortest path (max_hop)
  3. Alert fingerprinting (service|metric|severity string)

Schema matches the notebook (assignment.ipynb):
  - services.json nodes keyed by 'name', also has 'stores' nodes
  - edges use 'from' / 'to' / 'type'
  - cluster output includes max_severity, alert_ids, fingerprints
  - cluster_id is sequential: c-001, c-002, ...
  - fingerprint() returns raw "service|metric|severity" string (not a hash)
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import networkx as nx

logger = logging.getLogger("aiops.correlate")

SEVERITY_RANK = {"info": 0, "warn": 1, "warning": 1, "crit": 2, "critical": 2}


# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------

def build_graph_from_json(path: str | Path) -> nx.DiGraph:
    """
    Load services.json and return a directed NetworkX graph.

    Expected schema (matches notebook dataset):
      {
        "services": [{"name": "payment-svc", ...}, ...],
        "stores":   [{"name": "postgres-main", ...}, ...],
        "edges":    [{"from": "checkout-svc", "to": "payment-svc", "type": "http"}, ...]
      }
    """
    data = json.loads(Path(path).read_text())
    G = nx.DiGraph()

    for svc in data.get("services", []):
        G.add_node(svc["name"], kind="service")

    for store in data.get("stores", []):
        G.add_node(store["name"], kind="store")

    for edge in data.get("edges", []):
        G.add_edge(
            edge["from"],
            edge["to"],
            edge_type=edge.get("type", ""),
        )

    logger.info(
        "Service graph loaded: %d nodes, %d edges",
        G.number_of_nodes(),
        G.number_of_edges(),
    )
    return G


# ---------------------------------------------------------------------------
# Fingerprinting  (raw string, NOT a hash — matches notebook)
# ---------------------------------------------------------------------------

def fingerprint(alert: dict[str, Any]) -> str:
    """
    Return a stable string fingerprint for an alert.
    Format: "service|metric|severity"
    Two alerts with same service/metric/severity → same fingerprint.
    """
    return (
        f"{alert.get('service', '')}|"
        f"{alert.get('metric', '')}|"
        f"{alert.get('severity', '')}"
    )


# ---------------------------------------------------------------------------
# Temporal session grouping  (matches notebook session_groups)
# ---------------------------------------------------------------------------

def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def session_groups(
    alerts: list[dict[str, Any]],
    gap_sec: int = 120,
) -> list[list[dict[str, Any]]]:
    """
    Split sorted alerts into sessions.
    A new session starts when the gap between consecutive alerts > gap_sec.
    """
    if not alerts:
        return []

    alerts = sorted(alerts, key=lambda a: _parse_ts(a["ts"]))
    sessions: list[list[dict]] = [[alerts[0]]]

    for alert in alerts[1:]:
        current_ts = _parse_ts(alert["ts"])
        previous_ts = _parse_ts(sessions[-1][-1]["ts"])
        gap = (current_ts - previous_ts).total_seconds()

        if gap <= gap_sec:
            sessions[-1].append(alert)
        else:
            sessions.append([alert])

    return sessions


# ---------------------------------------------------------------------------
# Topology grouping via union-find + shortest path  (matches notebook)
# ---------------------------------------------------------------------------

def topology_group(
    alerts: list[dict[str, Any]],
    graph: nx.DiGraph,
    max_hop: int = 2,
) -> list[list[dict[str, Any]]]:
    """
    Group alerts whose services are within max_hop of each other in the graph.

    Uses union-find over unique services, merging pairs whose undirected
    shortest-path distance ≤ max_hop.  Matches the notebook implementation.
    """
    if not alerts:
        return []

    undirected = graph.to_undirected()

    by_service: dict[str, list[dict]] = defaultdict(list)
    for a in alerts:
        by_service[a["service"]].append(a)

    services = list(by_service.keys())

    # Union-Find
    parent = {s: s for s in services}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]   # path compression
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    for i, s1 in enumerate(services):
        for s2 in services[i + 1:]:
            try:
                dist = nx.shortest_path_length(undirected, s1, s2)
                if dist <= max_hop:
                    union(s1, s2)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                pass

    groups: dict[str, list[dict]] = defaultdict(list)
    for svc in services:
        groups[find(svc)].extend(by_service[svc])

    return list(groups.values())


# ---------------------------------------------------------------------------
# Main correlation pipeline  (matches notebook correlate)
# ---------------------------------------------------------------------------

def correlate(
    alerts: list[dict[str, Any]],
    graph: nx.DiGraph,
    gap_sec: int = 120,
    max_hop: int = 2,
) -> list[dict[str, Any]]:
    """
    Correlate a batch of alerts into incident clusters.

    Algorithm:
      1. Temporal windowing  → sessions
      2. Topology grouping   → groups per session (union-find + shortest path)
      3. Build cluster dicts with max_severity, alert_ids, fingerprints

    Returns:
        List of cluster dicts ordered by creation (primary cluster first).
        Each dict has:
          cluster_id, alert_count, services, time_range,
          max_severity, alert_ids, fingerprints, alerts
    """
    if not alerts:
        return []

    sessions = session_groups(alerts, gap_sec=gap_sec)
    clusters: list[dict[str, Any]] = []
    cluster_no = 1

    for session in sessions:
        topo_groups = topology_group(session, graph, max_hop=max_hop)

        for group in topo_groups:
            max_sev = max(
                group,
                key=lambda a: SEVERITY_RANK.get(a.get("severity", "info"), 0),
            )["severity"]

            clusters.append({
                "cluster_id": f"c-{cluster_no:03d}",
                "alert_count": len(group),
                "services": sorted({a["service"] for a in group}),
                "time_range": [
                    min(a["ts"] for a in group),
                    max(a["ts"] for a in group),
                ],
                "max_severity": max_sev,
                "alert_ids": [a["id"] for a in group],
                "fingerprints": sorted({fingerprint(a) for a in group}),
                # keep full alert dicts for RCA prompt — not in notebook output
                # but needed by rca.py; stripped before API response in pipeline.py
                "alerts": group,
            })

            cluster_no += 1

    return clusters