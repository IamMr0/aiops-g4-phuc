"""
serve.py — AIOps Incident Pipeline HTTP Service.

Endpoints:
  GET  /healthz    Liveness probe
  GET  /readyz     Readiness probe (checks graph + history loaded)
  GET  /version    App version + pipeline config + graph metadata
  GET  /metrics    Prometheus metrics (scraped by Prometheus)
  POST /incident   Main pipeline endpoint

Run:
  uvicorn serve:app --host 0.0.0.0 --port 8000 --reload           # dev
  uvicorn serve:app --host 0.0.0.0 --port 8000 --workers 4        # prod
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

# Load .env before anything else reads env vars
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from prometheus_client import Counter, Histogram, make_asgi_app, REGISTRY

import pipeline as pipe

# ---------------------------------------------------------------------------
# Logging — structured JSON
# ---------------------------------------------------------------------------

import json as _json


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        obj: dict = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        if hasattr(record, "extra"):
            obj.update(record.extra)
        return _json.dumps(obj)


_handler = logging.StreamHandler()
_handler.setFormatter(_JsonFormatter())
logging.basicConfig(handlers=[_handler], level=logging.INFO)
logger = logging.getLogger("aiops.serve")

# ---------------------------------------------------------------------------
# App version
# ---------------------------------------------------------------------------

APP_VERSION = os.environ.get("AIOPS_VERSION", "1.0.0")

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

REQUEST_COUNT = Counter(
    "aiops_incident_requests_total",
    "Total /incident requests",
    ["status"],
)
REQUEST_LATENCY = Histogram(
    "aiops_incident_latency_seconds",
    "End-to-end pipeline latency in seconds",
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)
CLUSTER_COUNT = Histogram(
    "aiops_clusters_per_request",
    "Number of clusters produced per request",
    buckets=[0, 1, 2, 5, 10, 20],
)
LLM_FAILURES = Counter(
    "aiops_llm_failures_total",
    "LLM call failures",
    ["reason"],
)

# ---------------------------------------------------------------------------
# Lifespan — init pipeline state on startup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    logger.info("Starting AIOps pipeline service v%s", APP_VERSION)
    pipe.init_state()
    yield
    logger.info("Shutting down AIOps pipeline service")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AIOps Incident Pipeline",
    version=APP_VERSION,
    description="Correlate alerts → RCA → suggest action",
    lifespan=lifespan,
)

# Mount Prometheus metrics endpoint
app.mount("/metrics", make_asgi_app())


# ---------------------------------------------------------------------------
# Latency middleware
# ---------------------------------------------------------------------------


@app.middleware("http")
async def add_timing(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Response-Time-Ms"] = f"{duration_ms:.1f}"
    logger.info(
        "%s %s %d %.1fms",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


# ---------------------------------------------------------------------------
# Pydantic schemas — Input
# ---------------------------------------------------------------------------


class Alert(BaseModel):
    id: str = Field(..., description="Unique alert ID")
    ts: str = Field(..., description="ISO-8601 timestamp e.g. 2026-06-12T09:42:01Z")
    service: str = Field(..., description="Service that fired the alert")
    metric: str = Field(..., description="Metric name e.g. latency_p99_ms")
    severity: str = Field(..., description="critical | warning | info")
    value: float = Field(..., description="Observed metric value")
    threshold: float = Field(..., description="Alert threshold value")
    labels: Optional[dict] = Field(default_factory=dict, description="Extra labels")


class IncidentRequest(BaseModel):
    alerts: list[Alert] = Field(..., min_length=1, description="Batch of alerts to process")


# ---------------------------------------------------------------------------
# Pydantic schemas — Output
# ---------------------------------------------------------------------------


class Cluster(BaseModel):
    cluster_id: str
    alert_count: int
    services: list[str]
    time_range: list[str]


class RootCause(BaseModel):
    service: str
    confidence: float
    reasoning: str


class SimilarIncident(BaseModel):
    id: str
    similarity: float
    summary: str


class IncidentResponse(BaseModel):
    clusters: list[Cluster]
    root_cause: RootCause
    recommended_actions: list[str]
    similar_incidents: list[SimilarIncident]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/healthz", tags=["ops"])
def healthz() -> dict:
    """
    Liveness probe.
    Returns 200 as long as the process is running.
    Use this for Kubernetes livenessProbe — if this fails, restart the pod.
    """
    return {"status": "ok"}


@app.get("/readyz", tags=["ops"])
def readyz() -> dict:
    """
    Readiness probe.
    Returns 503 if graph or history not yet loaded.
    Use this for Kubernetes readinessProbe during rolling deploys — Kubernetes
    will not route traffic to a pod that returns 503 here.
    """
    graph_info = pipe.get_graph_info()
    checks = {
        "graph": graph_info["graph_node_count"] > 0,
        "history": pipe.is_ready(),
    }

    # Optional: check LLM reachability (don't make readiness depend on external service)
    use_llm = os.environ.get("AIOPS_USE_LLM", "true").lower() == "true"
    if use_llm:
        try:
            from groq import Groq
            Groq(timeout=2.0).models.list()
            checks["llm"] = True
        except Exception:
            # LLM unavailable is NOT a readiness blocker (we have graph fallback)
            checks["llm"] = False
    else:
        checks["llm"] = "disabled"

    all_critical_ok = checks["graph"] and checks["history"]
    if not all_critical_ok:
        raise HTTPException(status_code=503, detail={"status": "not_ready", "checks": checks})

    return {"status": "ready", "checks": checks}


@app.get("/version", tags=["ops"])
def version() -> dict:
    """
    Returns app version, pipeline config, and current graph metadata.
    Useful for debugging: check this first when correlation quality degrades —
    graph_version tells you if a topology change caused a regression.
    """
    graph_info = pipe.get_graph_info()
    return {
        "app": APP_VERSION,
        "pipeline_config": {
            "correlate_gap_sec": pipe.CORRELATE_GAP_SEC,
            "correlate_max_hop": pipe.CORRELATE_MAX_HOP,
            "llm_enabled": os.environ.get("AIOPS_USE_LLM", "true").lower() == "true",
            "llm_model": os.environ.get("AIOPS_LLM_MODEL", "llama-3.3-70b-versatile"),
        },
        **graph_info,
    }


@app.post(
    "/incident",
    response_model=IncidentResponse,
    tags=["pipeline"],
    summary="Process a batch of alerts → incident report",
)
def post_incident(req: IncidentRequest) -> IncidentResponse:
    """
    Main pipeline endpoint.

    - Accepts a batch of alerts
    - Correlates them into clusters (temporal + graph proximity)
    - Runs Root Cause Analysis (graph PageRank + optional LLM enrichment)
    - Returns structured incident report with recommended actions

    Latency budget: p99 ≤ 10s (LLM call is the dominant cost ~2-8s).
    Invalid input returns 422 automatically via Pydantic — never 500.
    """
    logger.info(
        "Received incident request",
        extra={"extra": {"alert_count": len(req.alerts)}},
    )

    alerts_dict = [a.model_dump() for a in req.alerts]

    with REQUEST_LATENCY.time():
        try:
            result = pipe.process_batch(alerts_dict)
            REQUEST_COUNT.labels(status="success").inc()
            CLUSTER_COUNT.observe(len(result.get("clusters", [])))
            logger.info(
                "Incident processed",
                extra={
                    "extra": {
                        "cluster_count": len(result["clusters"]),
                        "root_cause": result["root_cause"]["service"],
                        "confidence": result["root_cause"]["confidence"],
                    }
                },
            )
            return IncidentResponse(**result)
        except HTTPException:
            raise
        except Exception as e:
            REQUEST_COUNT.labels(status="error").inc()
            logger.error("Pipeline failed: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail=f"Pipeline error: {e}")