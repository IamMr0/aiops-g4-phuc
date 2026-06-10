"""
pipeline.py — Glue layer: correlate → rca → enrich.

Loads graph + history once at module import (cached in process memory).
Reload thread refreshes the graph every GRAPH_RELOAD_INTERVAL_SEC seconds
to handle topology drift without restarting.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import networkx as nx

from correlate import build_graph_from_json, correlate
from rca import run_rca, rca_combined

logger = logging.getLogger("aiops.pipeline")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_HERE = Path(__file__).parent

SERVICES_JSON = os.environ.get("AIOPS_SERVICES_JSON", str(_HERE / "dataset/services.json"))
HISTORY_JSON = os.environ.get("AIOPS_HISTORY_JSON", str(_HERE / "dataset/incidents_history.json"))
GRAPH_RELOAD_INTERVAL_SEC = int(os.environ.get("AIOPS_GRAPH_RELOAD_SEC", "300"))  # 5 min

CORRELATE_GAP_SEC = int(os.environ.get("AIOPS_CORRELATE_GAP_SEC", "120"))
CORRELATE_MAX_HOP = int(os.environ.get("AIOPS_CORRELATE_MAX_HOP", "2"))

# ---------------------------------------------------------------------------
# Shared state (process-local; each uvicorn worker has its own copy)
# ---------------------------------------------------------------------------

_state_lock = threading.Lock()

_GRAPH: nx.DiGraph = nx.DiGraph()
_GRAPH_VERSION: str = "unloaded"
_GRAPH_LOADED_AT: str = "never"
_GRAPH_SOURCE: str = SERVICES_JSON

_HISTORY: list[dict[str, Any]] = []


def _load_graph() -> nx.DiGraph:
    return build_graph_from_json(SERVICES_JSON)


def _load_history() -> list[dict[str, Any]]:
    data = json.loads(Path(HISTORY_JSON).read_text(encoding="utf-8"))
    return data.get("incidents", [])


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _graph_version_from_file() -> str:
    """Derive a version string from file mtime."""
    try:
        mtime = Path(SERVICES_JSON).stat().st_mtime
        return "g-" + str(int(mtime))
    except Exception:
        return "g-unknown"


def init_state() -> None:
    """Called once at startup (from serve.py lifespan)."""
    global _GRAPH, _GRAPH_VERSION, _GRAPH_LOADED_AT, _HISTORY
    with _state_lock:
        _GRAPH = _load_graph()
        _GRAPH_VERSION = _graph_version_from_file()
        _GRAPH_LOADED_AT = _now_iso()
        _HISTORY = _load_history()
    logger.info("Pipeline state initialised. Graph: %s", _GRAPH_VERSION)
    _start_reload_thread()


def _reload_graph() -> None:
    """Periodically reload graph to handle topology drift (Strategy 1 from notes)."""
    global _GRAPH, _GRAPH_VERSION, _GRAPH_LOADED_AT
    while True:
        time.sleep(GRAPH_RELOAD_INTERVAL_SEC)
        try:
            new_graph = _load_graph()
            new_version = _graph_version_from_file()
            with _state_lock:
                _GRAPH = new_graph
                _GRAPH_VERSION = new_version
                _GRAPH_LOADED_AT = _now_iso()
            logger.info("Service graph reloaded: %s", new_version)
        except Exception as e:
            logger.error("Graph reload failed: %s", e)


def _start_reload_thread() -> None:
    t = threading.Thread(target=_reload_graph, daemon=True, name="graph-reload")
    t.start()
    logger.info(
        "Graph reload thread started (interval=%ds)", GRAPH_RELOAD_INTERVAL_SEC
    )


def get_graph_info() -> dict[str, Any]:
    with _state_lock:
        return {
            "graph_version": _GRAPH_VERSION,
            "graph_loaded_at": _GRAPH_LOADED_AT,
            "graph_source": _GRAPH_SOURCE,
            "graph_node_count": _GRAPH.number_of_nodes(),
            "graph_edge_count": _GRAPH.number_of_edges(),
        }


def is_ready() -> bool:
    with _state_lock:
        return _GRAPH.number_of_nodes() > 0 and len(_HISTORY) > 0


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def process_batch(alerts: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Full pipeline: correlate → RCA → enrich.

    Args:
        alerts: list of alert dicts (already validated by Pydantic in serve.py)

    Returns:
        dict matching IncidentResponse schema
    """
    with _state_lock:
        graph = _GRAPH
        history = _HISTORY

    # L1: Correlate
    clusters = correlate(
        alerts,
        graph,
        gap_sec=CORRELATE_GAP_SEC,
        max_hop=CORRELATE_MAX_HOP,
    )

    if not clusters:
        return {
            "clusters": [],
            "root_cause": {
                "service": "unknown",
                "confidence": 0.0,
                "reasoning": "No clusters formed from the provided alerts.",
            },
            "recommended_actions": ["Review alert thresholds — no correlated pattern detected."],
            "similar_incidents": [],
        }

    # Primary incident = largest cluster
    primary = max(clusters, key=lambda c: c["alert_count"])

    # L2 + L3: RCA + LLM enrichment
    rca_result = run_rca(primary, alerts, graph, history)

    return {
        "clusters": [
            {
                "cluster_id": c["cluster_id"],
                "alert_count": c["alert_count"],
                "services": c["services"],
                "time_range": c["time_range"],
            }
            for c in clusters
        ],
        "root_cause": {
            "service": rca_result["root_cause"],
            "confidence": rca_result["confidence"],
            "reasoning": rca_result.get("reasoning", ""),
        },
        "recommended_actions": rca_result.get("actions", []),
        "similar_incidents": [
            {
                "id": inc_id,
                "similarity": 0.75,
                "summary": next(
                    (h["summary"] for h in history if h["id"] == inc_id),
                    "Historical incident — see runbook.",
                ),
            }
            for inc_id in rca_result.get("similar_incidents", [])[:3]
        ],
    }