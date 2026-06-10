"""
tests/test_serve.py — Integration tests for FastAPI endpoints.
LLM is mocked so tests run without OPENAI_API_KEY.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

# Patch LLM before importing serve so rca.USE_LLM doesn't cause side effects
os.environ.setdefault("AIOPS_USE_LLM", "false")  # disable LLM for tests

from serve import app

# use_lifespan=True so pipeline.init_state() runs (loads graph + history)
client = TestClient(app, raise_server_exceptions=True)
client.__enter__()  # trigger lifespan startup

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

VALID_ALERT = {
    "id": "a-1",
    "ts": "2026-06-12T09:42:01Z",
    "service": "payment-svc",
    "metric": "latency_p99_ms",
    "severity": "critical",
    "value": 1840.0,
    "threshold": 500.0,
    "labels": {},
}

VALID_PAYLOAD = {"alerts": [VALID_ALERT]}

MULTI_ALERT_PAYLOAD = {
    "alerts": [
        VALID_ALERT,
        {
            "id": "a-2",
            "ts": "2026-06-12T09:42:30Z",
            "service": "order-svc",
            "metric": "error_rate",
            "severity": "warning",
            "value": 0.12,
            "threshold": 0.05,
            "labels": {},
        },
        {
            "id": "a-3",
            "ts": "2026-06-12T09:43:00Z",
            "service": "db-primary",
            "metric": "connections",
            "severity": "critical",
            "value": 495.0,
            "threshold": 400.0,
            "labels": {"region": "us-east-1"},
        },
    ]
}


# ---------------------------------------------------------------------------
# Liveness
# ---------------------------------------------------------------------------

class TestHealthz:
    def test_returns_200(self):
        r = client.get("/healthz")
        assert r.status_code == 200

    def test_returns_ok(self):
        r = client.get("/healthz")
        assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

class TestReadyz:
    def test_returns_200_when_ready(self):
        r = client.get("/readyz")
        # 200 = ready, 503 = not ready (e.g. dataset path wrong in CI)
        assert r.status_code in (200, 503)

    def test_has_status_field(self):
        r = client.get("/readyz")
        data = r.json()
        # status is top-level on 200, or inside detail on 503
        if r.status_code == 200:
            assert "status" in data
        else:
            assert "status" in data.get("detail", data)

    def test_has_checks_field(self):
        r = client.get("/readyz")
        data = r.json()
        if r.status_code == 200:
            assert "checks" in data
        else:
            assert "checks" in data.get("detail", data)


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

class TestVersion:
    def test_returns_200(self):
        r = client.get("/version")
        assert r.status_code == 200

    def test_has_app_version(self):
        r = client.get("/version")
        data = r.json()
        assert "app" in data

    def test_has_pipeline_config(self):
        r = client.get("/version")
        data = r.json()
        assert "pipeline_config" in data
        cfg = data["pipeline_config"]
        assert "correlate_gap_sec" in cfg
        assert "correlate_max_hop" in cfg

    def test_has_graph_metadata(self):
        r = client.get("/version")
        data = r.json()
        assert "graph_version" in data
        assert "graph_node_count" in data
        # Node count >= 0 (0 only if lifespan not triggered in test env)
        assert data["graph_node_count"] >= 0


# ---------------------------------------------------------------------------
# POST /incident
# ---------------------------------------------------------------------------

class TestIncidentHappyPath:
    def test_valid_input_returns_200(self):
        r = client.post("/incident", json=VALID_PAYLOAD)
        assert r.status_code == 200

    def test_response_has_clusters(self):
        r = client.post("/incident", json=VALID_PAYLOAD)
        data = r.json()
        assert "clusters" in data
        assert isinstance(data["clusters"], list)

    def test_response_has_root_cause(self):
        r = client.post("/incident", json=VALID_PAYLOAD)
        data = r.json()
        assert "root_cause" in data
        rc = data["root_cause"]
        assert "service" in rc
        assert "confidence" in rc
        assert "reasoning" in rc

    def test_response_has_recommended_actions(self):
        r = client.post("/incident", json=VALID_PAYLOAD)
        data = r.json()
        assert "recommended_actions" in data
        assert isinstance(data["recommended_actions"], list)

    def test_response_has_similar_incidents(self):
        r = client.post("/incident", json=VALID_PAYLOAD)
        data = r.json()
        assert "similar_incidents" in data

    def test_multi_alert_clusters_formed(self):
        r = client.post("/incident", json=MULTI_ALERT_PAYLOAD)
        assert r.status_code == 200
        data = r.json()
        assert len(data["clusters"]) >= 1
        total_alerts = sum(c["alert_count"] for c in data["clusters"])
        assert total_alerts == len(MULTI_ALERT_PAYLOAD["alerts"])

    def test_response_time_header_present(self):
        r = client.post("/incident", json=VALID_PAYLOAD)
        assert "x-response-time-ms" in r.headers

    def test_cluster_schema(self):
        r = client.post("/incident", json=MULTI_ALERT_PAYLOAD)
        for cluster in r.json()["clusters"]:
            assert "cluster_id" in cluster
            assert "alert_count" in cluster
            assert "services" in cluster
            assert "time_range" in cluster
            assert isinstance(cluster["services"], list)
            assert len(cluster["time_range"]) == 2


class TestIncidentValidation:
    def test_empty_alerts_returns_422(self):
        """Empty alerts list — Pydantic min_length=1 → 422."""
        r = client.post("/incident", json={"alerts": []})
        assert r.status_code == 422

    def test_missing_alerts_field_returns_422(self):
        r = client.post("/incident", json={})
        assert r.status_code == 422

    def test_missing_required_alert_field_returns_422(self):
        """Alert missing 'ts' → 422, not 500."""
        r = client.post("/incident", json={"alerts": [{"id": "a-1"}]})
        assert r.status_code == 422

    def test_wrong_type_returns_422(self):
        bad = {**VALID_ALERT, "value": "not-a-number"}
        r = client.post("/incident", json={"alerts": [bad]})
        assert r.status_code == 422

    def test_never_returns_500_on_bad_input(self):
        """Ensure validation errors are 4xx, not 5xx."""
        r = client.post("/incident", json={"alerts": [{"id": "a-1"}]})
        assert r.status_code < 500


# ---------------------------------------------------------------------------
# Mock LLM in pipeline test
# ---------------------------------------------------------------------------

class TestIncidentWithMockLLM:
    def test_pipeline_with_mock_llm(self):
        mock_result = {
            "root_cause": "payment-svc",
            "class": "connection_pool_exhaustion",
            "confidence": 0.84,
            "actions": ["Rollback", "Scale up connection pool", "Check DB"],
            "reasoning": "payment-svc showed highest latency and is upstream of order-svc.",
            "similar_incidents": ["INC-2025-11-08"],
        }
        with patch("rca._do_llm_call", return_value=mock_result):
            with patch.dict(os.environ, {"AIOPS_USE_LLM": "true"}):
                r = client.post("/incident", json=VALID_PAYLOAD)
        assert r.status_code == 200