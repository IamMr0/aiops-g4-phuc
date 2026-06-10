"""
tests/test_correlate.py — Unit tests for the correlation layer.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from correlate import fingerprint, session_groups, correlate, build_graph_from_json


SERVICES_JSON = os.path.join(os.path.dirname(__file__), "../dataset/services.json")

# Alerts use severity values matching the notebook: "crit" / "warn" / "info"
ALERT_PAYMENT_1 = {
    "id": "a-1",
    "ts": "2026-06-12T09:42:00Z",
    "service": "payment-svc",
    "metric": "latency_p99_ms",
    "severity": "crit",
    "value": 1840.0,
    "threshold": 500.0,
}
ALERT_PAYMENT_2 = {
    "id": "a-2",
    "ts": "2026-06-12T09:42:30Z",   # 30s later — same session
    "service": "payment-svc",
    "metric": "latency_p99_ms",
    "severity": "crit",
    "value": 1900.0,
    "threshold": 500.0,
}
ALERT_CHECKOUT = {
    "id": "a-3",
    "ts": "2026-06-12T09:43:00Z",   # 90s later — same session
    "service": "checkout-svc",
    "metric": "error_rate",
    "severity": "warn",
    "value": 0.12,
    "threshold": 0.05,
}
ALERT_DISTANT = {
    "id": "a-4",
    "ts": "2026-06-12T10:30:00Z",   # 48 min later — new session
    "service": "auth-svc",
    "metric": "cpu_percent",
    "severity": "warn",
    "value": 92.0,
    "threshold": 80.0,
}


class TestFingerprint:
    def test_same_service_metric_severity_different_ts(self):
        """Fingerprint must be identical despite different timestamps and values."""
        a = {**ALERT_PAYMENT_1, "ts": "2026-06-12T09:42:01Z", "value": 1840}
        b = {**ALERT_PAYMENT_1, "ts": "2026-06-12T09:42:30Z", "value": 1900}
        assert fingerprint(a) == fingerprint(b)

    def test_different_service_different_fingerprint(self):
        assert fingerprint(ALERT_PAYMENT_1) != fingerprint(ALERT_CHECKOUT)

    def test_fingerprint_format(self):
        """Fingerprint is 'service|metric|severity' — raw string, not a hash."""
        fp = fingerprint(ALERT_PAYMENT_1)
        assert fp == "payment-svc|latency_p99_ms|crit"

    def test_fingerprint_is_string(self):
        assert isinstance(fingerprint(ALERT_PAYMENT_1), str)


class TestSessionGroups:
    def test_single_alert(self):
        sessions = session_groups([ALERT_PAYMENT_1])
        assert len(sessions) == 1
        assert len(sessions[0]) == 1

    def test_two_alerts_within_gap_same_session(self):
        """30s gap < 120s → same session."""
        sessions = session_groups([ALERT_PAYMENT_1, ALERT_PAYMENT_2], gap_sec=120)
        assert len(sessions) == 1

    def test_distant_alert_new_session(self):
        """48 min gap > 120s → new session."""
        sessions = session_groups([ALERT_PAYMENT_1, ALERT_DISTANT], gap_sec=120)
        assert len(sessions) == 2

    def test_three_alerts_two_sessions(self):
        sessions = session_groups(
            [ALERT_PAYMENT_1, ALERT_PAYMENT_2, ALERT_CHECKOUT, ALERT_DISTANT],
            gap_sec=120,
        )
        assert len(sessions) == 2
        assert len(sessions[0]) == 3
        assert len(sessions[1]) == 1

    def test_empty_input(self):
        assert session_groups([]) == []

    def test_output_is_sorted_by_time(self):
        """Alerts should be sorted within each session."""
        # Feed in reverse order
        sessions = session_groups([ALERT_PAYMENT_2, ALERT_PAYMENT_1])
        assert sessions[0][0]["id"] == "a-1"
        assert sessions[0][1]["id"] == "a-2"


class TestCorrelate:
    @pytest.fixture
    def graph(self):
        return build_graph_from_json(SERVICES_JSON)

    def test_single_alert_one_cluster(self, graph):
        clusters = correlate([ALERT_PAYMENT_1], graph)
        assert len(clusters) == 1
        assert clusters[0]["alert_count"] == 1

    def test_related_services_grouped(self, graph):
        """checkout-svc → payment-svc are 1 hop apart → same cluster at max_hop=1."""
        alerts = [ALERT_PAYMENT_1, ALERT_PAYMENT_2, ALERT_CHECKOUT]
        clusters = correlate(alerts, graph, gap_sec=120, max_hop=1)
        assert clusters[0]["alert_count"] == 3

    def test_distant_alert_separate_cluster(self, graph):
        """Different sessions → separate clusters."""
        alerts = [ALERT_PAYMENT_1, ALERT_DISTANT]
        clusters = correlate(alerts, graph, gap_sec=120, max_hop=2)
        assert len(clusters) == 2

    def test_cluster_id_sequential(self, graph):
        """cluster_id must be c-001, c-002, ... (not hash-based)."""
        clusters = correlate(
            [ALERT_PAYMENT_1, ALERT_PAYMENT_2, ALERT_CHECKOUT, ALERT_DISTANT],
            graph, gap_sec=120, max_hop=1,
        )
        ids = [c["cluster_id"] for c in clusters]
        assert ids[0] == "c-001"
        assert ids[1] == "c-002"

    def test_empty_alerts(self, graph):
        assert correlate([], graph) == []

    def test_cluster_has_all_required_keys(self, graph):
        clusters = correlate([ALERT_PAYMENT_1], graph)
        required = {
            "cluster_id", "alert_count", "services",
            "time_range", "max_severity", "alert_ids", "fingerprints",
        }
        for c in clusters:
            assert required.issubset(c.keys())

    def test_fingerprints_field_is_raw_strings(self, graph):
        """fingerprints in cluster output must be 'svc|metric|severity' strings."""
        clusters = correlate([ALERT_PAYMENT_1], graph)
        fp = clusters[0]["fingerprints"][0]
        assert "|" in fp
        assert "payment-svc" in fp

    def test_max_severity_crit(self, graph):
        alerts = [ALERT_PAYMENT_1, ALERT_CHECKOUT]   # crit + warn → crit
        clusters = correlate(alerts, graph, gap_sec=120, max_hop=1)
        assert clusters[0]["max_severity"] == "crit"

    def test_alert_ids_present(self, graph):
        clusters = correlate([ALERT_PAYMENT_1, ALERT_PAYMENT_2], graph)
        ids = clusters[0]["alert_ids"]
        assert "a-1" in ids
        assert "a-2" in ids