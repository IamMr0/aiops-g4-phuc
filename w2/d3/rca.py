"""
rca.py — Root Cause Analysis layer.

Two-stage pipeline:
  L2 — Graph-based RCA: PageRank on reverse subgraph of alerting services
  L3 — LLM enrichment: call LLM to classify failure, suggest actions,
       find similar incidents. Skipped when AIOPS_USE_LLM=false or
       graph confidence ≥ 0.9.

Environment variables:
  GROQ_API_KEY     — required for LLM calls (set in .env)
  AIOPS_USE_LLM    — 'true' (default) | 'false' to disable LLM
  AIOPS_LLM_MODEL  — default 'llama-3.3-70b-versatile' (Groq)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

import networkx as nx
from cachetools import TTLCache

logger = logging.getLogger("aiops.rca")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

USE_LLM: bool = os.environ.get("AIOPS_USE_LLM", "true").lower() == "true"
LLM_MODEL: str = os.environ.get("AIOPS_LLM_MODEL", "llama-3.3-70b-versatile")
LLM_CONFIDENCE_SKIP_THRESHOLD = 0.9  # skip LLM if graph is already very confident

import threading

_llm_cache: TTLCache = TTLCache(maxsize=1000, ttl=3600)
_llm_cache_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Graph-based RCA (L2)
# ---------------------------------------------------------------------------

def _graph_rca(
    cluster: dict[str, Any],
    graph: nx.DiGraph,
) -> list[tuple[str, float]]:
    """
    PageRank on the *reverse* subgraph of alerting services.

    Intuition: in a call graph, a downstream failure propagates upstream.
    Reversing the graph makes the root cause the highest-ranked node.

    Returns:
        List of (service, confidence) sorted by confidence desc.
    """
    alerting_services = set(cluster.get("services", []))
    if not alerting_services:
        return []

    # Build subgraph: include alerting services + their direct predecessors
    relevant = set(alerting_services)
    for svc in alerting_services:
        if graph.has_node(svc):
            relevant.update(graph.predecessors(svc))
            relevant.update(graph.successors(svc))

    subgraph = graph.subgraph(relevant).copy()

    if subgraph.number_of_nodes() == 0:
        return [(list(alerting_services)[0], 0.5)]

    # Reverse for upstream propagation analysis
    rev = subgraph.reverse()

    try:
        scores = nx.pagerank(rev, alpha=0.85, max_iter=100)
    except nx.PowerIterationFailedConvergence:
        scores = {n: 1.0 / len(rev.nodes) for n in rev.nodes}

    # Only score alerting services; normalise to [0, 1]
    alerting_scores = {
        svc: scores.get(svc, 0.0) for svc in alerting_services if graph.has_node(svc)
    }
    if not alerting_scores:
        alerting_scores = {svc: 1.0 for svc in alerting_services}

    total = sum(alerting_scores.values()) or 1.0
    ranked = sorted(
        [(svc, score / total) for svc, score in alerting_scores.items()],
        key=lambda x: x[1],
        reverse=True,
    )
    return ranked


# ---------------------------------------------------------------------------
# LLM enrichment (L3)
# ---------------------------------------------------------------------------

def _build_llm_prompt(
    cluster: dict[str, Any],
    graph_candidates: list[tuple[str, float]],
    history: list[dict[str, Any]],
) -> str:
    top_candidates = graph_candidates[:3]
    history_summary = [
        {"id": inc["id"], "root_cause": inc["root_cause"],
         "class": inc["class"], "summary": inc["summary"]}
        for inc in history[:10]
    ]

    prompt = f"""You are an SRE expert performing root cause analysis for a production incident.

## Alerting cluster
Services involved: {cluster.get('services', [])}
Alert count: {cluster.get('alert_count', 0)}
Max severity: {cluster.get('max_severity', 'unknown')}
Time range: {cluster.get('time_range', [])}
Alert fingerprints: {cluster.get('fingerprints', [])}

## Sample alerts
{json.dumps(cluster.get('alerts', [])[:5], indent=2)}

## Graph-based RCA candidates
{json.dumps([{'service': s, 'confidence': round(c, 3)} for s, c in top_candidates], indent=2)}

## Historical incidents (for similar incident matching)
{json.dumps(history_summary, indent=2)}

Respond ONLY with a valid JSON object (no markdown, no preamble) with these exact keys:
{{
  "root_cause": "<service_id>",
  "class": "<failure_class e.g. connection_pool_exhaustion, memory_leak, disk_io_saturation, replication_lag, cache_eviction, network_timeout, cpu_throttle, dependency_timeout>",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<2-3 sentence explanation>",
  "actions": ["<action1>", "<action2>", "<action3>"],
  "similar_incidents": ["<INC-ID1>", "<INC-ID2>"]
}}"""
    return prompt


def _do_llm_call(prompt: str) -> dict[str, Any]:
    """Call the LLM API. Raises on failure."""
    try:
        from groq import Groq
        client = Groq(timeout=10.0, max_retries=2)
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=600,
        )
        raw = response.choices[0].message.content.strip()
        # Extract JSON block if enclosed in markdown, ignoring preamble
        if "```" in raw:
            parts = raw.split("```")
            if len(parts) >= 3:
                raw = parts[1]
                if raw.lower().startswith("json"):
                    raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        logger.error("LLM call failed: %s", e)
        raise


def _cached_llm_call(prompt: str) -> dict[str, Any]:
    key = hashlib.sha256(prompt.encode()).hexdigest()
    with _llm_cache_lock:
        if key in _llm_cache:
            logger.debug("LLM cache hit")
            return _llm_cache[key]
            
    result = _do_llm_call(prompt)
    with _llm_cache_lock:
        _llm_cache[key] = result
    return result


# ---------------------------------------------------------------------------
# Combined RCA entry point
# ---------------------------------------------------------------------------

def rca_combined(
    cluster: dict[str, Any],
    graph: nx.DiGraph,
) -> list[tuple[str, float]]:
    """Return graph-only RCA candidates. Used when LLM is disabled."""
    return _graph_rca(cluster, graph)


def run_rca(
    cluster: dict[str, Any],
    all_alerts: list[dict[str, Any]],
    graph: nx.DiGraph,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Full L2+L3 RCA pipeline.

    Returns dict with keys:
      root_cause, confidence, reasoning, actions, similar_incidents, method
    """
    # L2: graph RCA
    graph_candidates = _graph_rca(cluster, graph)
    if not graph_candidates:
        graph_candidates = [("unknown", 0.0)]

    top_service, top_confidence = graph_candidates[0]

    # Skip LLM if disabled or graph is already very confident
    if not USE_LLM:
        logger.info("LLM disabled via flag, using graph-only RCA")
        return {
            "root_cause": top_service,
            "confidence": round(top_confidence, 3),
            "reasoning": f"Graph PageRank on reverse subgraph identified {top_service} as root cause.",
            "actions": _default_actions(top_service),
            "similar_incidents": _find_similar_by_service(top_service, history),
            "method": "graph-only-flag-off",
        }

    if top_confidence >= LLM_CONFIDENCE_SKIP_THRESHOLD:
        logger.info(
            "Graph confidence %.2f >= %.2f, skipping LLM enrichment",
            top_confidence, LLM_CONFIDENCE_SKIP_THRESHOLD,
        )
        return {
            "root_cause": top_service,
            "confidence": round(top_confidence, 3),
            "reasoning": f"High-confidence graph RCA: {top_service} is the upstream root cause.",
            "actions": _default_actions(top_service),
            "similar_incidents": _find_similar_by_service(top_service, history),
            "method": "graph-only-high-confidence",
        }

    # L3: LLM enrichment
    prompt = _build_llm_prompt(cluster, graph_candidates, history)
    try:
        llm_result = _cached_llm_call(prompt)
        logger.info(
            "LLM RCA complete: root_cause=%s confidence=%.2f method=llm",
            llm_result.get("root_cause"), llm_result.get("confidence", 0),
        )
        return {
            "root_cause": llm_result.get("root_cause", top_service),
            "confidence": float(llm_result.get("confidence", top_confidence)),
            "reasoning": llm_result.get("reasoning", ""),
            "actions": llm_result.get("actions", []),
            "similar_incidents": llm_result.get("similar_incidents", []),
            "method": "graph+llm",
        }
    except Exception as e:
        # LLM failed — graceful degradation to graph-only
        logger.warning("LLM enrichment failed, falling back to graph RCA: %s", e)
        return {
            "root_cause": top_service,
            "confidence": round(top_confidence, 3),
            "reasoning": f"Graph RCA (LLM unavailable): {top_service} ranked highest by PageRank on reverse subgraph.",
            "actions": _default_actions(top_service),
            "similar_incidents": _find_similar_by_service(top_service, history),
            "method": "graph-only-llm-fallback",
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_actions(service: str) -> list[str]:
    return [
        f"Check recent deployments for {service}",
        f"Review {service} error logs and metrics dashboard",
        "Escalate to on-call if not resolved in 15 minutes",
    ]


def _find_similar_by_service(
    service: str,
    history: list[dict[str, Any]],
) -> list[str]:
    """Return IDs of historical incidents with the same root cause service."""
    matches = [
        inc["id"] for inc in history
        if inc.get("root_cause") == service
    ]
    return matches[:3]