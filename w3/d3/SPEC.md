# AIOps Mini-Platform Spec — Ngo Nguyen Phuc

## 1. Platform overview

The platform monitors a small e-commerce-style service mesh — edge WAF, api-gateway,
checkout-svc, and payment-svc — using a metrics + alerting + correlation + RCA pipeline. Scope is
detection and root-cause analysis for incidents originating at the edge or in the request path
between gateway and payment processing. Users of the platform are the on-call SRE rotation and
the platform/edge engineering teams who own the services being monitored; output (alerts, RCA
verdicts, recommended actions) is consumed directly by whoever is paged during an incident.

## 2. SLO definition (from W3-D1)

Three services carry defined SLOs: api (availability+latency, target 99.9%), db (query
success+latency, target 99.95%), and frontend (page load, target 98.5%). Each SLO has a
corresponding SLI defined in `slo_spec.yaml` with error budget calculated as
`(1 - SLO) × total_events` per the standard definition. The api service combines availability
and latency (2xx/3xx AND latency<500ms), the db service measures query success with duration<100ms,
and the frontend service tracks page load success (dom_ready<3000ms AND no js/network errors).
Budget consumption for this specific incident is tracked qualitatively in `postmortem.md` → Impact,
since a full burn-rate calculation requires the production event-count baseline rather than the
reproduction's synthetic load.

## 3. Detection + Correlation + RCA stack (from W1+W2)

**Detection layer (W1):** OpenTelemetry instrumentation on services emits metrics (transaction latency,
error counts) via OTLP to the Otel Collector, which buffers data through Kafka to Apache Flink for
real-time stream processing. Flink computes rolling statistics and applies threshold-based anomaly
detection algorithms, storing results in VictoriaMetrics for querying via Grafana dashboards and alerts.

**Correlation layer (W2 L1):** Valid alerts are grouped temporally (gap_sec=120) and topologically
(max_hop=2) using the loaded service graph. This layer clusters symptom alerts by service/time
proximity but currently lacks awareness of deploy/change events — it doesn't anchor clusters to the
change that caused them. ADR-001 addresses this gap by adding change-event correlation.

**RCA layer (W2 L2+L3):** For the primary cluster, PageRank runs on the reversed subgraph of
alerting services to identify the upstream root cause deterministically (L2). An LLM enrichment
stage (L3) constructs a prompt with alerts, L2 candidates, and historical incidents to provide
human-readable reasoning and remediation suggestions. In the W3-D3 reproduction, the historical-
similarity signal overrode a stronger topology signal, and neither RCA pass identified the true root
cause (edge-waf). ADR-001 proposes adding change-event correlation as a higher-priority signal.

## 4. Reliability validation (from W3-D2)

Chaos engineering scoreboard (10 experiments):
- Detected: 9/10 (≥ 7 required)
- RCA correct: 8/9 (≥ 5 required)
- False alarms: 0 (≤ 1 required)
- Precision: 1.00
- Recall: 0.90
- MTTD p50: 2s, p95: 3s
- **Verdict: PASS**

Top 3 gaps identified:
1. **Monitoring Dependency Loop (Experiment 7: log_collector_disk_fill)** — The pipeline's detector
   relies on metrics flowing through the same log-collector it monitors. When the log-collector's
   disk fills, log ingestion stalls, starving the detector of input rather than providing a fault
   signal. Fix: add independent out-of-band heartbeat for log-collector health.
2. **DNS Root Attribution (Experiment 9: dns_lookup_latency)** — RCA incorrectly returned api-gateway
   instead of dns-resolver. The topology graph lacks DNS as a first-class dependency node below the
   service layer. Fix: add dns-resolver to dependency graph with temporal-causal ranking (Granger
   causality) to establish causal precedence.
3. **Similarity Override of Topology Signal** — Historical-similarity matching can override stronger
   topology-based candidate ranking, producing confidently-wrong root causes. This gap was observed
   in the W3-D3 Cloudflare reproduction where similarity picked payment-svc over the higher-scoring
   checkout-svc (0.735 vs 0.265). Fix: re-weight similarity to act as tiebreaker only.

## 5. Operational pattern (from W3-D3)

Reproduced outage: **Cloudflare WAF regex (catastrophic backtracking)**, outage catalog entry #3.
A WAF rule revision containing a regex vulnerable to catastrophic backtracking was promoted to the
edge middleware canary, pegging edge CPU and cascading into elevated gateway latency, checkout
timeouts, and payment queue growth. Full reproduction, timeline, and analysis are in
`postmortem.md`.

Key learning: the platform's RCA stage can correctly detect *that* an incident is happening
(alerts fire promptly) while being confidently wrong about *where* it started, because no signal
in the current pipeline checks "what changed right before this." A structurally available
correlation (the deploy event was right there in `timeline.json`) was never used.

ADR reference: **ADR-001** — Add deploy/change-event correlation as an RCA input signal, weighted
above historical-similarity matching, to close this gap.

## 6. Cost model (from W3-D3)

`cost_model.py` output for three scenarios:

| Scenario | num_services | incidents/mo | avg duration (h) | downtime $/h | AIOps $/mo | ROI | Payback (mo) | Verdict |
|---|---|---|---|---|---|---|---|---|
| Small stack | 20 | 2 | 1.0 | $10,000 | $15,000 | 0.53 | 1.88 | not_worth_it |
| Mid stack | 100 | 5 | 2.0 | $20,000 | $25,000 | 3.2 | 0.31 | worth_it |
| E-commerce (this platform's rough scale) | 500 | 10 | 1.5 | $50,000 | $60,000 | 5.0 | 0.20 | worth_it |

Break-even point for this platform's stack (using the e-commerce scenario as the closest proxy to
the services modeled in §1, since the reproduction's services are e-commerce-shaped): ROI 5.0,
meaning monthly value generated (~$300,000 in avoided downtime cost, assuming a 40% MTTR
reduction) is 5× the assumed $60,000/month AIOps cost. The platform is well above the worth_it
threshold (ROI > 1.5) at this incident volume and downtime cost — but that verdict is sensitive to
the `incidents_per_month` and `downtime_cost_per_hour` assumptions, neither of which has been
measured from this platform's actual production traffic yet.

## 7. Open risks

| Risk | Severity | Mitigation plan |
|---|---|---|
| RCA has no change-event correlation signal (root cause of the Cloudflare-pattern miss in this submission) | High | ADR-001: add change-event correlation, prioritized above similarity matching; target 2026-07-01 |
| Historical-similarity signal can override a stronger topology signal, producing a confidently-wrong root cause | High | Re-weight similarity to act as tiebreaker only, per postmortem action items; target 2026-07-08 |
| No pre-deploy regex complexity / ReDoS check on WAF rule promotions | Medium | Add ReDoS detector to WAF rule CI pipeline; target 2026-07-15 |
| WAF rules promote globally/atomically rather than via staged canary | Medium | Require 1% → 10% → 100% canary gating for all WAF rule changes; target 2026-07-15 |
| Cost model verdict depends on unmeasured production assumptions (incident rate, downtime cost/hour) | Medium | Instrument actual incident frequency and a defensible downtime-cost-per-hour figure from finance/product before relying on the ROI number for budget decisions |